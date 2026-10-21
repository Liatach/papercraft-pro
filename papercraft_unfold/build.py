# SPDX-License-Identifier: GPL-3.0-or-later
"""Scene objects: the live paper layout and the fold/assemble rig."""

import bpy
from mathutils import Matrix, Vector

from .mesh_io import ensure_collection

LAYOUT_COLLECTION = "Papercraft Layout"
RIG_COLLECTION = "Papercraft Fold Rig"
PAGE_GAP_MM = 15.0


# ----------------------------------------------------------------- helpers
def _material(name, rgba):
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
        mat.diffuse_color = rgba
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf is not None:
            bsdf.inputs["Base Color"].default_value = rgba
            rough = bsdf.inputs.get("Roughness")
            if rough is not None:
                rough.default_value = 1.0
    return mat


def _replace_mesh(obj_name, mesh_name, coll):
    obj = bpy.data.objects.get(obj_name)
    if obj is not None and obj.type == "MESH":
        mesh = obj.data
        mesh.clear_geometry()
        mesh.materials.clear()
    else:
        mesh = bpy.data.meshes.new(mesh_name)
        obj = bpy.data.objects.new(obj_name, mesh)
    if obj.name not in coll.objects:
        coll.objects.link(obj)
    return obj, mesh


class PageFrame:
    """Maps page millimetres to world space beside the source model."""

    def __init__(self, source_verts, lay):
        xs = [v[0] for v in source_verts] or [0.0]
        ys = [v[1] for v in source_verts] or [0.0]
        zs = [v[2] for v in source_verts] or [0.0]
        size = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs), 1e-6)
        self.unit = 1.0 / lay.scale if lay.scale > 0 else 1.0  # world units per mm
        self.x0 = max(xs) + size * 0.25
        self.y0 = (min(ys) + max(ys)) * 0.5 - lay.page_h * self.unit * 0.5
        self.z0 = min(zs)
        self.page_w = lay.page_w
        self.page_h = lay.page_h

    def to_rig(self, page, p):
        """Same piece, turned printed-side-down on the table in front of the
        layout (a 180 degree turn, not a mirror) so folds rise upwards."""
        v = self.to_world(page, p)
        yc = self.y0 + self.page_h * self.unit * 0.5
        shift = self.page_h * self.unit * 1.1
        return Vector((v.x, 2.0 * yc - v.y - shift, 2.0 * self.z0 - v.z))

    def to_world(self, page, p, lift=0.0):
        x = self.x0 + (page * (self.page_w + PAGE_GAP_MM) + p[0]) * self.unit
        y = self.y0 + p[1] * self.unit
        return Vector((x, y, self.z0 + lift * self.unit))


# ----------------------------------------------------------------- layout
def build_layout(scene, src, res, lay, name="Papercraft Layout"):
    """(Re)build the flat layout object: paper sheets, pieces, tabs."""
    coll = ensure_collection(scene, LAYOUT_COLLECTION)
    obj, mesh = _replace_mesh(name, name, coll)
    frame = PageFrame(src.verts, lay)

    verts, faces, mat_idx, uvs = [], [], [], []
    paper = _material("Papercraft Paper", (1.0, 1.0, 1.0, 1.0))
    tab_mat = _material("Papercraft Tab", (0.85, 0.80, 0.70, 1.0))
    warn_mat = _material("Papercraft Tab Warning", (0.9, 0.15, 0.1, 1.0))
    mats = [paper, tab_mat, warn_mat]
    slot_of_src = {}
    for i, m in enumerate(src.mat_list):
        if m is not None:
            if m not in mats:
                mats.append(m)
            slot_of_src[i] = mats.index(m)

    def add_poly(points, page, lift, mat, uv=None):
        base = len(verts)
        for p in points:
            verts.append(frame.to_world(page, p, lift))
        faces.append(list(range(base, base + len(points))))
        mat_idx.append(mat)
        uvs.append(uv if uv else [(0.0, 0.0)] * len(points))

    for page in range(max(lay.page_count, 1)):
        add_poly(
            [(0, 0), (lay.page_w, 0), (lay.page_w, lay.page_h), (0, lay.page_h)],
            page,
            0.0,
            0,
        )
    for tab in lay.tabs:
        place = lay.placements[tab.island]
        add_poly(lay.tab_points(tab), place.page, 0.05, 2 if tab.squashed else 1)
    for isl in res.islands:
        place = lay.placements[isl.index]
        for f in isl.order:
            face = res.faces[f]
            uv = _face_uv(src, face)
            add_poly(
                lay.face_points(res, isl, f),
                place.page,
                0.1,
                slot_of_src.get(face.material, 0),
                uv,
            )

    mesh.from_pydata(verts, [], faces)
    for m in mats:
        mesh.materials.append(m)
    mesh.polygons.foreach_set("material_index", mat_idx)
    uv_layer = mesh.uv_layers.new(name="UVMap")
    flat = [c for poly_uv in uvs for uv in poly_uv for c in uv]
    uv_layer.data.foreach_set("uv", flat)
    mesh.update()
    obj.show_wire = True
    obj.show_all_edges = True
    obj["papercraft_source"] = src_name_hint(scene)
    return obj


def src_name_hint(scene):
    props = scene.papercraft
    return props.source.name if props.source else ""


# ----------------------------------------------------------------- rig
def _best_corner(pts):
    """Corner whose triple is best conditioned (largest cross product)."""
    best_k, best_n = 0, -1.0
    count = len(pts)
    for k in range(count):
        a, b, c = pts[k], pts[(k + 1) % count], pts[(k + 2) % count]
        n = (b - a).cross(c - a).length
        if n > best_n * (1.0 + 1e-6):
            best_n, best_k = n, k
    return best_k


def _frame_from_points(pts, k):
    """Orthonormal frame (as a 4x4) from corner triple k. Use the same k for
    both point sets so the two frames correspond."""
    count = len(pts)
    a, b, c = pts[k], pts[(k + 1) % count], pts[(k + 2) % count]
    n = (b - a).cross(c - a)
    u = (b - a).normalized()
    n = n.normalized()
    w = n.cross(u)
    m = Matrix.Identity(4)
    for r in range(3):
        m[r][0], m[r][1], m[r][2], m[r][3] = u[r], w[r], n[r], a[r]
    return m


def remove_rig():
    for name in ("Papercraft Rig", "Papercraft Folding"):
        obj = bpy.data.objects.get(name)
        if obj is not None:
            data = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if data is not None and data.users == 0:
                if isinstance(data, bpy.types.Armature):
                    bpy.data.armatures.remove(data)
                else:
                    bpy.data.meshes.remove(data)
    coll = bpy.data.collections.get(RIG_COLLECTION)
    if coll is not None:
        for obj in list(coll.objects):
            if obj.name.startswith("Papercraft Target"):
                bpy.data.objects.remove(obj, do_unlink=True)


def _add_driver(target_id, data_path, index, owner, prop, factor):
    fc = target_id.driver_add(data_path, index) if index >= 0 else target_id.driver_add(data_path)
    drv = fc.driver
    drv.type = "SCRIPTED"
    var = drv.variables.new()
    var.name = "t"
    var.type = "SINGLE_PROP"
    var.targets[0].id_type = "OBJECT"
    var.targets[0].id = owner
    var.targets[0].data_path = f'["{prop}"]'
    # A "simple expression": evaluated without Python, so it works even
    # when Auto Run Python Scripts is off.
    drv.expression = f"t*{factor:.9f}" if factor != 1.0 else "t"
    return fc


def build_rig(context, src, res, lay, include_tabs=True):
    """Armature with one bone per face (and per tab). Bone Y axes lie on the
    hinge edges, so each fold is a single driven Y rotation:
        fold     0 = flat on the page, 1 = folded into shape
        assemble 0 = pieces stay on the page, 1 = pieces fly to the model
    """
    scene = context.scene
    remove_rig()
    coll = ensure_collection(scene, RIG_COLLECTION)
    frame = PageFrame(src.verts, lay)

    arm_data = bpy.data.armatures.new("Papercraft Rig")
    arm = bpy.data.objects.new("Papercraft Rig", arm_data)
    coll.objects.link(arm)
    arm.show_in_front = False
    arm_data.display_type = "STICK"
    arm["fold"] = 0.0
    arm["assemble"] = 0.0
    for prop, desc in (
        ("fold", "0 = flat sheet, 1 = folded into shape"),
        ("assemble", "0 = pieces on the page, 1 = pieces moved onto the model"),
    ):
        ui = arm.id_properties_ui(prop)
        ui.update(min=0.0, max=1.0, soft_min=0.0, soft_max=1.0, description=desc)

    mesh = bpy.data.meshes.new("Papercraft Folding")
    folding = bpy.data.objects.new("Papercraft Folding", mesh)
    coll.objects.link(folding)

    # Previous mode bookkeeping for edit-bone creation.
    view_layer = context.view_layer
    prev_active = view_layer.objects.active
    prev_mode = context.mode
    if prev_mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    view_layer.objects.active = arm
    arm.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")

    world_pts = {}  # (island, face) -> [Vector]
    for isl in res.islands:
        page = lay.placements[isl.index].page
        for f in isl.faces:
            world_pts[f] = [frame.to_rig(page, p) for p in lay.face_points(res, isl, f)]

    bone_of_face = {}
    hinges = []  # (bone name, angle)
    roots = []  # (bone name, island)
    ebones = arm_data.edit_bones
    min_len = 1e-4 * frame.unit
    for isl in res.islands:
        for f in isl.order:
            face = res.faces[f]
            name = f"F{f}"
            eb = ebones.new(name)
            if f == isl.root:
                c = sum(world_pts[f], Vector()) / len(world_pts[f])
                eb.head = c
                eb.tail = c + Vector((0.0, 0.0, 10.0 * frame.unit))
                roots.append((name, isl))
            else:
                p, i, j, angle = isl.parent[f]
                P = res.faces[p]
                eb.head = world_pts[p][P.loop.index(i)]
                eb.tail = world_pts[p][P.loop.index(j)]
                if (eb.tail - eb.head).length < min_len:
                    eb.tail = eb.head + (eb.tail - eb.head).normalized() * min_len
                eb.parent = ebones[bone_of_face[p]]
                hinges.append((name, angle))
            eb.roll = 0.0
            bone_of_face[f] = name

    tab_bones = []
    if include_tabs:
        for n, tab in enumerate(lay.tabs):
            isl = res.islands[tab.island]
            face = res.faces[tab.face]
            k = tab.corner
            a = world_pts[tab.face][k]
            b = world_pts[tab.face][(k + 1) % len(face.loop)]
            name = f"T{n}"
            eb = ebones.new(name)
            eb.head, eb.tail = a, b
            eb.parent = ebones[bone_of_face[tab.face]]
            info = res.edges[_key(face, k)]
            tab_bones.append((name, tab, info.angle))
            hinges.append((name, info.angle))

    rest = {eb.name: eb.matrix.copy() for eb in ebones}
    bpy.ops.object.mode_set(mode="OBJECT")

    # Drivers on hinge rotations.
    for name, angle in hinges:
        pb = arm.pose.bones[name]
        pb.rotation_mode = "XYZ"
        _add_driver(pb, "rotation_euler", 1, arm, "fold", angle)

    # Root bones: blend towards the model's pose with a Copy Transforms.
    for name, isl in roots:
        f = isl.root
        face = res.faces[f]
        model_pts = [Vector(src.verts[v]) for v in face.loop]
        k = _best_corner(model_pts)
        T = _frame_from_points(model_pts, k) @ _frame_from_points(world_pts[f], k).inverted()
        target = bpy.data.objects.new(f"Papercraft Target I{isl.index}", None)
        target.empty_display_type = "PLAIN_AXES"
        target.empty_display_size = 0.0
        target.hide_select = True
        coll.objects.link(target)
        target.matrix_world = T @ rest[name]
        con = arm.pose.bones[name].constraints.new("COPY_TRANSFORMS")
        con.target = target
        con.influence = 0.0
        _add_driver(con, "influence", -1, arm, "assemble", 1.0)

    # Skinned mesh: every face/tab gets its own vertices on its own bone.
    verts, faces, groups, mat_idx, uvs = [], [], [], [], []
    for isl in res.islands:
        for f in isl.order:
            face = res.faces[f]
            base = len(verts)
            verts.extend(world_pts[f])
            faces.append(list(range(base, base + len(face.loop))))
            groups.append(bone_of_face[f])
            mat_idx.append(face.material)
            uvs.append(_face_uv(src, face) or [(0.0, 0.0)] * len(face.loop))
    tab_slot = len(src.mat_list)
    for name, tab, _ in tab_bones:
        page = lay.placements[tab.island].page
        pts = [frame.to_rig(page, p) for p in lay.tab_points(tab)]
        base = len(verts)
        verts.extend(pts)
        faces.append(list(range(base, base + len(pts))))
        groups.append(name)
        mat_idx.append(tab_slot)
        uvs.append([(0.0, 0.0)] * len(pts))

    mesh.from_pydata([tuple(v) for v in verts], [], faces)
    for m in src.mat_list:
        mesh.materials.append(m)
    mesh.materials.append(_material("Papercraft Tab", (0.85, 0.80, 0.70, 1.0)))
    mesh.polygons.foreach_set("material_index", mat_idx)
    uv_layer = mesh.uv_layers.new(name="UVMap")
    uv_layer.data.foreach_set("uv", [c for poly in uvs for uv in poly for c in uv])
    mesh.update()
    vgroups = {}
    for poly, gname in zip(mesh.polygons, groups):
        vg = vgroups.get(gname)
        if vg is None:
            vg = vgroups[gname] = folding.vertex_groups.new(name=gname)
        vg.add(list(poly.vertices), 1.0, "REPLACE")
    mod = folding.modifiers.new("Fold Rig", "ARMATURE")
    mod.object = arm
    folding.parent = arm

    # Restore selection / mode.
    arm.select_set(False)
    if prev_active is not None and prev_active.name in view_layer.objects:
        view_layer.objects.active = prev_active
    if prev_mode == "EDIT_MESH" and prev_active is not None and prev_active.type == "MESH":
        bpy.ops.object.mode_set(mode="EDIT")
    return arm, folding


def _face_uv(src, face):
    if not src.uvs or face.source >= len(src.uvs):
        return None
    poly_uv = src.uvs[face.source]
    return [poly_uv[c] for c in face.corners]


def _key(face, k):
    a, b = face.loop[k], face.loop[(k + 1) % len(face.loop)]
    return (a, b) if a < b else (b, a)


def keyframe_animation(arm, start, fold_end, assemble_end):
    for prop, frames in (
        ("fold", ((start, 0.0), (fold_end, 1.0))),
        ("assemble", ((start, 0.0), (fold_end, 0.0), (assemble_end, 1.0))),
    ):
        for frame, value in frames:
            arm[prop] = value
            arm.keyframe_insert(data_path=f'["{prop}"]', frame=frame)
    arm["fold"] = 0.0
    arm["assemble"] = 0.0

