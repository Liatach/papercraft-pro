# SPDX-License-Identifier: GPL-3.0-or-later
"""Bake whatever the model's materials show onto the printed pages.

For each page a temporary mesh is built from exactly the pieces on that
page. It keeps the model's own UV maps and colour attributes (so layered or
painted materials, e.g. Paint System, image textures, procedurals and vertex
colours all shade as they do on the model) plus one extra UV map that places
every face where it sits on the page. Cycles bakes the material colour
through that UV map into a page-sized image.
"""

import base64

import bpy
from mathutils import Vector

from .build import write_corner_layers
from .core.svg import Texture
from .textures import _encode_pixels

BAKE_UV = "PapercraftPage"
PASSES = {
    "BASE": ("DIFFUSE", {"COLOR"}),
    "EMIT": ("EMIT", set()),
    "COMBINED": ("COMBINED", set()),
}


class _SceneState:
    """Temporarily switch to Cycles bake settings; restore on exit."""

    def __init__(self, scene, samples):
        self.scene = scene
        self.samples = samples

    def __enter__(self):
        s = self.scene
        self.engine = s.render.engine
        s.render.engine = "CYCLES"
        self.cycles_samples = s.cycles.samples
        s.cycles.samples = self.samples
        bake = s.render.bake
        self.bake = (bake.use_selected_to_active, bake.use_clear, bake.margin, bake.target)
        bake.use_selected_to_active = False
        return self

    def __exit__(self, *exc):
        s = self.scene
        s.render.engine = self.engine
        s.cycles.samples = self.cycles_samples
        bake = s.render.bake
        bake.use_selected_to_active, bake.use_clear, bake.margin, bake.target = self.bake
        return False


def _fallback_material(src_mat):
    """Materials without nodes can't take a bake target: stand in a simple
    node material in the same colour."""
    mat = bpy.data.materials.new("Papercraft Bake Stand-in")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is not None and src_mat is not None:
        bsdf.inputs["Base Color"].default_value = tuple(src_mat.diffuse_color)
    return mat


def _page_mesh(src_obj, entry, items, name):
    src, res, lay = entry.src, entry.res, entry.lay
    inv = src_obj.matrix_world.inverted()
    verts, faces, mats, corners, page_uv = [], [], [], [], []
    for isl, f in items:
        face = res.faces[f]
        base = len(verts)
        verts.extend(tuple(inv @ Vector(src.verts[v])) for v in face.loop)
        faces.append(list(range(base, base + len(face.loop))))
        mats.append(face.material)
        corners.append(face)
        for x, y in lay.face_points(res, isl, f):
            page_uv.extend((x / lay.page_w, y / lay.page_h))
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    write_corner_layers(mesh, src, corners)
    if len(mesh.uv_layers) >= 8:  # Blender's limit; drop the last to make room
        mesh.uv_layers.remove(mesh.uv_layers[-1])
    uv = mesh.uv_layers.new(name=BAKE_UV)
    uv.data.foreach_set("uv", page_uv)
    mesh.polygons.foreach_set("material_index", mats)
    mesh.update()
    return mesh


def bake_pages(context, src_obj, entry, dpi=200, bake_pass="BASE", margin_mm=0.3,
               samples=None, progress=None):
    """Return one Texture (data: URI PNG) per page, or None for empty pages."""
    scene = context.scene
    res, lay, src = entry.res, entry.lay, entry.src
    width = max(16, round(lay.page_w / 25.4 * dpi))
    height = max(16, round(lay.page_h / 25.4 * dpi))
    margin_px = max(1, round(margin_mm / 25.4 * dpi))
    bake_type, pass_filter = PASSES.get(bake_pass, PASSES["BASE"])
    if samples is None:
        samples = 32 if bake_type == "COMBINED" else 4

    per_page = {}
    for isl in res.islands:
        page = lay.placements[isl.index].page
        for f in isl.order:
            per_page.setdefault(page, []).append((isl, f))

    view_layer = context.view_layer
    prev_selected = [o.name for o in context.selected_objects]
    prev_active = view_layer.objects.active.name if view_layer.objects.active else None
    if context.mode != "OBJECT" and view_layer.objects.active is not None:
        bpy.ops.object.mode_set(mode="OBJECT")

    # Materials used by the model, with a bake target node in each.
    slot_mats = []
    temp_mats = []
    # Read materials from the object now: cached references can be stale.
    live_mats = [slot.material for slot in src_obj.material_slots]
    for m in live_mats or [None]:
        if m is None or not m.use_nodes or m.node_tree is None:
            m = _fallback_material(m)
            temp_mats.append(m)
        slot_mats.append(m)

    results = []
    with _SceneState(scene, samples):
        for page in range(max(lay.page_count, 1)):
            items = per_page.get(page)
            if not items:
                results.append(None)
                continue
            if progress:
                progress(page)
            mesh = _page_mesh(src_obj, entry, items, f"Papercraft Bake {page}")
            for m in slot_mats:
                mesh.materials.append(m)
            obj = bpy.data.objects.new("Papercraft Bake", mesh)
            obj.matrix_world = src_obj.matrix_world
            scene.collection.objects.link(obj)
            image = bpy.data.images.new(
                f"Papercraft Page {page + 1}", width, height, alpha=True
            )
            image.generated_color = (0.0, 0.0, 0.0, 0.0)
            added = []
            try:
                for m in slot_mats:
                    nodes = m.node_tree.nodes
                    node = nodes.new("ShaderNodeTexImage")
                    node.image = image
                    added.append((m, node, nodes.active))
                    nodes.active = node
                for o in context.selected_objects:
                    o.select_set(False)
                obj.select_set(True)
                view_layer.objects.active = obj
                kwargs = dict(
                    type=bake_type,
                    margin=margin_px,
                    use_clear=True,
                    uv_layer=BAKE_UV,
                    target="IMAGE_TEXTURES",
                )
                if pass_filter:
                    kwargs["pass_filter"] = pass_filter
                try:
                    kwargs["margin_type"] = "EXTEND"
                    bpy.ops.object.bake(**kwargs)
                except TypeError:
                    kwargs.pop("margin_type")
                    bpy.ops.object.bake(**kwargs)
                png = _encode_pixels(image, max(width, height))
                uri = f"data:image/png;base64,{base64.b64encode(png).decode('ascii')}"
                results.append(Texture(uri, width, height))
            finally:
                for m, node, prev in added:
                    m.node_tree.nodes.remove(node)
                    if prev is not None:
                        m.node_tree.nodes.active = prev
                bpy.data.objects.remove(obj, do_unlink=True)
                bpy.data.meshes.remove(mesh)
                bpy.data.images.remove(image)

    for m in temp_mats:
        bpy.data.materials.remove(m)
    for o in context.selected_objects:
        o.select_set(False)
    for name in prev_selected:
        o = bpy.data.objects.get(name)
        if o is not None and o.name in view_layer.objects:
            o.select_set(True)
    if prev_active and prev_active in view_layer.objects:
        view_layer.objects.active = bpy.data.objects[prev_active]
    return results
