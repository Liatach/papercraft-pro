# SPDX-License-Identifier: GPL-3.0-or-later
"""Scene objects for each model: the live paper layout and the fold rig."""

import hashlib

import bpy
from mathutils import Matrix, Vector

from . import targets as T
from .core.geom import polygon_area

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


def model_bounds(verts):
    xs = [v[0] for v in verts] or [0.0]
    ys = [v[1] for v in verts] or [0.0]
    zs = [v[2] for v in verts] or [0.0]
    size = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs), 1e-6)
    return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs)), size


class PageFrame:
    """Page millimetres -> the layout object's space -> world.

    The layout object can be moved/rotated freely; everything (highlights,
    rig) follows it."""

    def __init__(self, lay, layout_name):
        self.unit = 1.0 / lay.scale if lay.scale > 0 else 1.0  # world units per mm
        self.page_w = lay.page_w
        self.page_h = lay.page_h
        self.layout_name = layout_name

    @property
    def matrix(self):
        obj = bpy.data.objects.get(self.layout_name)
        return obj.matrix_world.copy() if obj is not None else Matrix.Identity(4)

    def to_local(self, page, p, lift=0.0):
        return Vector((
            (page * (self.page_w + PAGE_GAP_MM) + p[0]) * self.unit,
            p[1] * self.unit,
            lift * self.unit,
        ))

    def to_world(self, page, p, lift=0.0):
        return self.matrix @ self.to_local(page, p, lift)

    def to_rig(self, page, p, matrix=None):
        """The rig's sheet: same layout, printed side up, laid on the table
        just in front of the layout so the two don't overlap."""
        v = self.to_local(page, p)
        h = self.page_h * self.unit
        shifted = Vector((v.x, v.y - h * 1.1, v.z))
        return (matrix if matrix is not None else self.matrix) @ shifted


def default_layout_location(verts, lay):
    lo, hi, size = model_bounds(verts)
    unit = 1.0 / lay.scale if lay.scale > 0 else 1.0
    return Vector((hi[0] + size * 0.25, (lo[1] + hi[1]) * 0.5 - lay.page_h * unit * 0.5, lo[2]))


def _corner_values(item, per_poly, blank):
    """Per-corner values for one new polygon (see write_corner_layers)."""
    if isinstance(item, int):
        return [blank] * item
    if isinstance(item, tuple):  # ("interp", face, weights): an outside tab
        _, face, weights = item
        poly = per_poly[face.source]
        vals = [poly[c] for c in face.corners]
        size = len(blank)
        return [
            tuple(sum(vals[c][i] * w for c, w in ws) for i in range(size)) for ws in weights
        ]
    poly = per_poly[item.source]
    return [poly[c] for c in item.corners]


def tab_corner_source(tab, res):
    """Corner source for a tab polygon: covered art if it's an outside tab."""
    if getattr(tab, "outer", False) and tab.partner is not None and tab.weights:
        return ("interp", res.faces[tab.partner[0]], tab.weights)
    return len(tab.points)


def write_corner_layers(mesh, src, corner_sources):
    """Copy every UV map and colour attribute from the model.

    corner_sources: per new polygon either a res Face (copy its corners), an
    int n (n blank corners, e.g. tabs and page sheets) or ("interp", face,
    weights) to blend a face's corners (outside tabs show the art they
    cover)."""
    for name, per_poly in src.uv_layers.items():
        if len(mesh.uv_layers) >= 8:
            break
        flat = []
        for item in corner_sources:
            for uv in _corner_values(item, per_poly, (0.0, 0.0)):
                flat.extend(uv[:2])
        layer = mesh.uv_layers.new(name=name)
        layer.data.foreach_set("uv", flat)
    for uv in mesh.uv_layers:
        uv.active_render = uv.name == src.render_uv
    if src.render_uv and src.render_uv in mesh.uv_layers:
        mesh.uv_layers.active = mesh.uv_layers[src.render_uv]
    for name, per_poly in src.color_layers.items():
        flat = []
        for item in corner_sources:
            for col in _corner_values(item, per_poly, (1.0, 1.0, 1.0, 1.0)):
                col = tuple(col) + (1.0,) * (4 - len(col))
                flat.extend(col[:4])
        try:
            attr = mesh.color_attributes.new(name, "FLOAT_COLOR", "CORNER")
        except RuntimeError:
            continue
        attr.data.foreach_set("color", flat)
    if src.active_color and src.active_color in mesh.color_attributes:
        try:
            mesh.color_attributes.active_color_name = src.active_color
        except AttributeError:
            mesh.color_attributes.active_color = mesh.color_attributes[src.active_color]
        try:
            mesh.color_attributes.render_color_index = list(
                mesh.color_attributes.keys()
            ).index(src.active_color)
        except (AttributeError, ValueError):
            pass


# ----------------------------------------------------------------- layout
def build_layout(scene, src_obj, src, res, lay):
    """(Re)build the model's flat layout object: paper sheets, pieces, tabs."""
    obj = T.output(src_obj, T.ROLE_LAYOUT)
    created = obj is None or obj.type != "MESH"
    if created:
        mesh = bpy.data.meshes.new(f"{src_obj.name} Layout")
        obj = bpy.data.objects.new(f"{src_obj.name} Layout", mesh)
        T.tag(obj, T.ROLE_LAYOUT, src_obj)
        obj.location = default_layout_location(src.verts, lay)
    else:
        mesh = obj.data
        mesh.clear_geometry()
        mesh.materials.clear()
        for attr in list(mesh.color_attributes):
            mesh.color_attributes.remove(attr)
        while mesh.uv_layers:
            mesh.uv_layers.remove(mesh.uv_layers[0])
    coll = T.collection_for(scene, src_obj)
    if obj.name not in coll.objects:
        coll.objects.link(obj)
    frame = PageFrame(lay, obj.name)

    verts, faces, mat_idx, corners = [], [], [], []
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

    def add_poly(points, page, lift, mat, corner_src):
        base = len(verts)
        for p in points:
            verts.append(frame.to_local(page, p, lift))
        faces.append(list(range(base, base + len(points))))
        mat_idx.append(mat)
        corners.append(corner_src)

    poly_map = []  # layout polygon -> ("page", n) | ("tab", i) | ("face", f)
    for page in range(max(lay.page_count, 1)):
        add_poly(
            [(0, 0), (lay.page_w, 0), (lay.page_w, lay.page_h), (0, lay.page_h)],
            page, -0.6, 0, 4,  # sheet sits clearly under the pieces
        )
        poly_map.append(("page", page))
    for n, tab in enumerate(lay.tabs):
        place = lay.placements[tab.island]
        pts = lay.tab_points(tab)
        corner_src = tab_corner_source(tab, res)
        mat = 2 if tab.squashed else 1
        if not isinstance(corner_src, int):  # outside tab: covered art
            mat = slot_of_src.get(res.faces[tab.partner[0]].material, 1)
        if polygon_area(pts) < 0:  # face up like everything else
            pts = pts[::-1]
            if not isinstance(corner_src, int):
                corner_src = (corner_src[0], corner_src[1], corner_src[2][::-1])
        add_poly(pts, place.page, -0.2 if isinstance(corner_src, int) else 0.05, mat, corner_src)
        poly_map.append(("tab", n))
    for isl in res.islands:
        place = lay.placements[isl.index]
        for f in isl.order:
            face = res.faces[f]
            add_poly(
                lay.face_points(res, isl, f), place.page, 0.0,
                slot_of_src.get(face.material, 0), face,
            )
            poly_map.append(("face", f))

    mesh.from_pydata([tuple(v) for v in verts], [], faces)
    for m in mats:
        mesh.materials.append(m)
    mesh.polygons.foreach_set("material_index", mat_idx)
    write_corner_layers(mesh, src, corners)
    mesh.update()
    obj.show_wire = True
    obj.show_all_edges = True
    return obj, poly_map, frame


def reset_layout_position(src_obj, entry):
    obj = T.output(src_obj, T.ROLE_LAYOUT)
    if obj is not None:
        obj.matrix_world = Matrix.Translation(default_layout_location(entry.src.verts, entry.lay))


def remove_layout(src_obj):
    for obj in T.outputs(src_obj, T.ROLE_LAYOUT):
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if data is not None and data.users == 0:
            bpy.data.meshes.remove(data)


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


def rig_signature(entry):
    h = hashlib.sha1()
    h.update(entry.signature.encode())
    h.update(repr(entry.settings).encode())
    return h.hexdigest()[:16]


def rig_is_stale(src_obj, entry):
    arm = T.output(src_obj, T.ROLE_RIG)
    if arm is None or entry is None:
        return False
    return arm.get("papercraft_signature") != rig_signature(entry)


def ensure_object_mode(context):
    """Leave Edit/Pose/Paint modes safely. Returns (active name, mode) to
    restore afterwards."""
    view_layer = context.view_layer
    active = view_layer.objects.active
    mode = context.mode
    if mode != "OBJECT" and active is not None:
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except RuntimeError:
            pass
    return (active.name if active is not None else None), mode


def restore_mode(context, saved):
    name, mode = saved
    obj = bpy.data.objects.get(name) if name else None
    if obj is None or obj.name not in context.view_layer.objects:
        return
    context.view_layer.objects.active = obj
    if mode == "EDIT_MESH" and obj.type == "MESH":
        try:
            bpy.ops.object.mode_set(mode="EDIT")
        except RuntimeError:
            pass


def remove_rig(src_obj):
    """Delete the model's rig. Returns its animation so a rebuild can keep it."""
    saved = None
    arm = T.output(src_obj, T.ROLE_RIG)
    if arm is not None:
        ad = arm.animation_data
        saved = {
            "action": ad.action if ad else None,
            "slot": getattr(ad, "action_slot", None) if ad else None,
            "fold": float(arm.get("fold", 0.0)),
            "assemble": float(arm.get("assemble", 0.0)),
        }
        if saved["action"] is not None:
            saved["action"].use_fake_user = True  # survive while the rig is gone
    for role in (T.ROLE_FOLDING, T.ROLE_RIG, T.ROLE_TARGET):
        for obj in T.outputs(src_obj, role):
            data = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if data is not None and data.users == 0:
                if isinstance(data, bpy.types.Armature):
                    bpy.data.armatures.remove(data)
                elif isinstance(data, bpy.types.Mesh):
                    bpy.data.meshes.remove(data)
    return saved


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


def build_rig(context, src_obj, entry, include_tabs=True):
    """Armature with one bone per face (and per tab). Bone Y axes lie on the
    hinge edges, so each fold is a single driven Y rotation:
        fold     0 = flat on the page, 1 = folded into shape
        assemble 0 = pieces stay on the page, 1 = pieces fly to the model
    Rebuilding keeps the previous rig's keyframes."""
    src, res, lay = entry.src, entry.res, entry.lay
    scene = context.scene
    saved_mode = ensure_object_mode(context)
    saved_anim = remove_rig(src_obj)
    coll = T.collection_for(scene, src_obj)
    layout_obj = T.output(src_obj, T.ROLE_LAYOUT)
    frame = entry.frame or PageFrame(lay, layout_obj.name if layout_obj else "")
    layout_matrix = frame.matrix

    arm_data = bpy.data.armatures.new(f"{src_obj.name} Rig")
    arm = bpy.data.objects.new(f"{src_obj.name} Rig", arm_data)
    T.tag(arm, T.ROLE_RIG, src_obj)
    coll.objects.link(arm)
    arm_data.display_type = "STICK"
    arm["fold"] = 0.0
    arm["assemble"] = 0.0
    for prop, desc in (
        ("fold", "0 = flat sheet, 1 = folded into shape"),
        ("assemble", "0 = pieces on the page, 1 = pieces moved onto the model"),
    ):
        ui = arm.id_properties_ui(prop)
        ui.update(min=0.0, max=1.0, soft_min=0.0, soft_max=1.0, description=desc)

    mesh = bpy.data.meshes.new(f"{src_obj.name} Folding")
    folding = bpy.data.objects.new(f"{src_obj.name} Folding", mesh)
    T.tag(folding, T.ROLE_FOLDING, src_obj)
    coll.objects.link(folding)

    world_pts = {}
    for isl in res.islands:
        page = lay.placements[isl.index].page
        for f in isl.faces:
            world_pts[f] = [
                frame.to_rig(page, p, layout_matrix) for p in lay.face_points(res, isl, f)
            ]

    view_layer = context.view_layer
    for o in context.selected_objects:
        o.select_set(False)
    view_layer.objects.active = arm
    arm.select_set(True)
    bone_of_face, hinges, roots, tab_bones = {}, [], [], []
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        ebones = arm_data.edit_bones
        min_len = 1e-4 * frame.unit
        for isl in res.islands:
            for f in isl.order:
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
        if include_tabs:
            for n, tab in enumerate(lay.tabs):
                face = res.faces[tab.face]
                k = tab.corner
                name = f"T{n}"
                eb = ebones.new(name)
                eb.head = world_pts[tab.face][k]
                eb.tail = world_pts[tab.face][(k + 1) % len(face.loop)]
                eb.parent = ebones[bone_of_face[tab.face]]
                info = res.edges[_key(face, k)]
                tab_bones.append((name, tab))
                hinges.append((name, info.angle))
        rest = {eb.name: eb.matrix.copy() for eb in ebones}
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")

    for name, angle in hinges:
        pb = arm.pose.bones[name]
        pb.rotation_mode = "XYZ"
        _add_driver(pb, "rotation_euler", 1, arm, "fold", angle)

    # Root bones: two Copy Transforms per piece.
    #   "Folded" (influence = fold): turn the piece into the model's own
    #   orientation, resting on the table above where it lay, so the folded
    #   model stands the right way up beside the layout.
    #   "Assembled" (influence = assemble): slide it into place on the model.
    table_z = (layout_matrix @ Vector((0.0, 0.0, 0.0))).z
    # Keep the many helper empties out of the way in one sub-collection.
    target_coll = None
    for child in coll.children:
        if child.get("papercraft_targets"):
            target_coll = child
            break
    if target_coll is None:
        target_coll = bpy.data.collections.new(f"{src_obj.name} Rig Targets")
        target_coll["papercraft_targets"] = True
        coll.children.link(target_coll)
    for name, isl in roots:
        f = isl.root
        face = res.faces[f]
        model_pts = [Vector(src.verts[v]) for v in face.loop]
        k = _best_corner(model_pts)
        M = _frame_from_points(model_pts, k) @ _frame_from_points(world_pts[f], k).inverted()
        island_model = [Vector(src.verts[v]) for g in isl.faces for v in res.faces[g].loop]
        island_sheet = [p for g in isl.faces for p in world_pts[g]]
        lo = Vector((min(p.x for p in island_model), min(p.y for p in island_model),
                     min(p.z for p in island_model)))
        hi = Vector((max(p.x for p in island_model), max(p.y for p in island_model), 0.0))
        sheet_c = sum(island_sheet, Vector()) / len(island_sheet)
        offset = Vector((sheet_c.x - (lo.x + hi.x) / 2, sheet_c.y - (lo.y + hi.y) / 2,
                         table_z - lo.z))
        for label, matrix, prop in (
            ("Folded", Matrix.Translation(offset) @ M, "fold"),
            ("Assembled", M, "assemble"),
        ):
            target = bpy.data.objects.new(f"{src_obj.name} {label} {isl.index}", None)
            T.tag(target, T.ROLE_TARGET, src_obj)
            target.empty_display_type = "PLAIN_AXES"
            target.empty_display_size = 0.0
            target.hide_select = True
            target_coll.objects.link(target)
            target.matrix_world = matrix @ rest[name]
            con = arm.pose.bones[name].constraints.new("COPY_TRANSFORMS")
            con.name = label
            con.target = target
            con.influence = 0.0
            _add_driver(con, "influence", -1, arm, prop, 1.0)

    verts, faces, groups, mat_idx, corners = [], [], [], [], []
    for isl in res.islands:
        for f in isl.order:
            face = res.faces[f]
            base = len(verts)
            verts.extend(world_pts[f])
            faces.append(list(range(base, base + len(face.loop))))
            groups.append(bone_of_face[f])
            mat_idx.append(face.material)
            corners.append(face)
    tab_slot = len(src.mat_list)
    for name, tab in tab_bones:
        page = lay.placements[tab.island].page
        flat_pts = lay.tab_points(tab)
        corner_src = tab_corner_source(tab, res)
        if polygon_area(flat_pts) < 0:  # face the same way as the pieces
            flat_pts = flat_pts[::-1]
            if not isinstance(corner_src, int):
                corner_src = (corner_src[0], corner_src[1], corner_src[2][::-1])
        pts = [frame.to_rig(page, p, layout_matrix) for p in flat_pts]
        base = len(verts)
        verts.extend(pts)
        faces.append(list(range(base, base + len(pts))))
        groups.append(name)
        if isinstance(corner_src, int):
            mat_idx.append(tab_slot)
        else:
            mat_idx.append(res.faces[tab.partner[0]].material)
        corners.append(corner_src)

    mesh.from_pydata([tuple(v) for v in verts], [], faces)
    for m in src.mat_list:
        mesh.materials.append(m)
    mesh.materials.append(_material("Papercraft Tab", (0.85, 0.80, 0.70, 1.0)))
    mesh.polygons.foreach_set("material_index", mat_idx)
    write_corner_layers(mesh, src, corners)
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

    arm["papercraft_signature"] = rig_signature(entry)
    if saved_anim:
        arm["fold"] = saved_anim["fold"]
        arm["assemble"] = saved_anim["assemble"]
        if saved_anim["action"] is not None:
            ad = arm.animation_data_create()
            ad.action = saved_anim["action"]
            slot = saved_anim["slot"]
            if slot is not None and hasattr(ad, "action_slot"):
                try:
                    ad.action_slot = slot
                except (TypeError, RuntimeError, ReferenceError):
                    pass
            saved_anim["action"].use_fake_user = False

    arm.select_set(False)
    restore_mode(context, saved_mode)
    return arm, folding


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
