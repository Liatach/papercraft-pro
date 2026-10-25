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
from .core.geom import polygon_area
from .core.svg import Texture
from .textures import _encode_pixels, polygon_mask

BAKE_UV = "PapercraftPage"
PASSES = {
    "BASE": ("DIFFUSE", {"COLOR"}),
    "EMIT": ("EMIT", set()),
    # Lighting without glossy reflections: paper is matte, and reflections
    # of the scene printed onto a piece just look like smudges.
    "COMBINED": ("COMBINED", {"EMIT", "DIRECT", "INDIRECT", "DIFFUSE", "TRANSMISSION"}),
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


# A known grey baked next to every page tells us how Blender stored the
# result: linear values (needs the sRGB curve) or already display values.
# Blender builds differ here, and guessing wrong washes colours out (sRGB
# applied twice) or darkens them.
CHECK_LINEAR = 0.2


def _check_object(scene, image):
    mesh = bpy.data.meshes.new("Papercraft Bake Check")
    mesh.from_pydata([(0, 0, 0), (0.01, 0, 0), (0.01, 0.01, 0), (0, 0.01, 0)], [], [[0, 1, 2, 3]])
    uv = mesh.uv_layers.new(name=BAKE_UV)
    uv.data.foreach_set("uv", (0, 0, 1, 0, 1, 1, 0, 1))
    mat = bpy.data.materials.new("Papercraft Bake Check")
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (CHECK_LINEAR, CHECK_LINEAR, CHECK_LINEAR, 1.0)
    bsdf.inputs["Roughness"].default_value = 1.0
    emit = nt.nodes.new("ShaderNodeEmission")
    emit.inputs["Color"].default_value = (CHECK_LINEAR, CHECK_LINEAR, CHECK_LINEAR, 1.0)
    emit.inputs["Strength"].default_value = 1.0
    node = nt.nodes.new("ShaderNodeTexImage")
    node.image = image
    nt.nodes.active = node
    mesh.materials.append(mat)
    obj = bpy.data.objects.new("Papercraft Bake Check", mesh)
    obj.location = (0.0, 0.0, -10000.0)  # far from the model: no shadows, no light
    for attr in ("visible_diffuse", "visible_glossy", "visible_transmission",
                 "visible_shadow", "visible_volume_scatter"):
        if hasattr(obj, attr):
            setattr(obj, attr, False)
    scene.collection.objects.link(obj)
    return obj, mat, bsdf, emit


def _check_shader(mat, bsdf, emit, pass_name):
    """Base colour pass reads the BSDF colour; emission/full render the
    emission (a full render of the BSDF would depend on the lights)."""
    nt = mat.node_tree
    out = next(n for n in nt.nodes if n.type == "OUTPUT_MATERIAL")
    src = bsdf if pass_name == "BASE" else emit
    nt.links.new(src.outputs[0], out.inputs["Surface"])


def _srgb_steps(image):
    """How many times the page pixels still need the sRGB curve: 1 if the
    check grey came back linear, 0 if as sRGB, -1 if sRGB was applied twice."""
    import numpy as np

    from .textures import to_srgb

    w, h = image.size
    px = np.empty(w * h * 4, dtype=np.float32)
    image.pixels.foreach_get(px)
    px = px.reshape(h, w, 4)[h // 4: 3 * h // 4, w // 4: 3 * w // 4].reshape(-1, 4)
    v = float(np.median(px[:, :3]))
    levels = [CHECK_LINEAR]
    for _ in range(2):
        levels.append(float(to_srgb(np.array(levels[-1]))))
    found = min(range(3), key=lambda k: abs(v - levels[k]))  # times encoded
    if abs(v - levels[found]) > 0.06:
        print(f"Papercraft: bake colour check read {v:.3f} (expected one of "
              f"{', '.join(f'{x:.3f}' for x in levels)})")
    return 1 - found


def textures_decode(context, check_colorspace="sRGB"):
    """Does this Blender's Cycles decode sRGB image textures? A bake of a
    plain grey next to an sRGB texture of the same grey must agree. Some
    builds (e.g. with a system OpenColorIO config) skip the decode, so
    painted textures render and bake pale while EEVEE shows them right.
    check_colorspace exists for tests."""
    import numpy as np

    from .textures import to_srgb

    scene = context.scene
    view_layer = context.view_layer
    out_img = bpy.data.images.new("Papercraft Decode Check", 16, 8, alpha=True)
    tex_img = bpy.data.images.new("Papercraft Decode Texture", 4, 4, alpha=False)
    srgb_grey = float(to_srgb(np.array(CHECK_LINEAR)))
    try:  # before the pixels: changing colour space regenerates the image
        tex_img.colorspace_settings.name = check_colorspace
    except TypeError:
        pass
    tex_img.pixels.foreach_set(np.array([srgb_grey, srgb_grey, srgb_grey, 1.0] * 16, np.float32))
    mesh = bpy.data.meshes.new("Papercraft Decode Check")
    mesh.from_pydata([(0, 0, 0), (0.01, 0, 0), (0.01, 0.01, 0), (0, 0.01, 0),
                      (0.02, 0, 0), (0.03, 0, 0), (0.03, 0.01, 0), (0.02, 0.01, 0)],
                     [], [[0, 1, 2, 3], [4, 5, 6, 7]])
    uv = mesh.uv_layers.new(name=BAKE_UV)
    uv.data.foreach_set("uv", (0, 0, 0.5, 0, 0.5, 1, 0, 1, 0.5, 0, 1, 0, 1, 1, 0.5, 1))
    mats = []
    for use_texture in (False, True):
        m = bpy.data.materials.new("Papercraft Decode Check")
        m.use_nodes = True
        nt = m.node_tree
        out = next(n for n in nt.nodes if n.type == "OUTPUT_MATERIAL")
        emit = nt.nodes.new("ShaderNodeEmission")
        emit.inputs["Color"].default_value = (CHECK_LINEAR, CHECK_LINEAR, CHECK_LINEAR, 1.0)
        if use_texture:
            t = nt.nodes.new("ShaderNodeTexImage")
            t.image = tex_img
            t.interpolation = "Closest"
            nt.links.new(t.outputs["Color"], emit.inputs["Color"])
        nt.links.new(emit.outputs[0], out.inputs["Surface"])
        target = nt.nodes.new("ShaderNodeTexImage")
        target.image = out_img
        nt.nodes.active = target
        mats.append(m)
        mesh.materials.append(m)
    mesh.polygons.foreach_set("material_index", [0, 1])
    obj = bpy.data.objects.new("Papercraft Decode Check", mesh)
    obj.location = (0.0, 0.0, -10000.0)
    scene.collection.objects.link(obj)
    prev_sel = [o for o in context.selected_objects]
    prev_active = view_layer.objects.active
    try:
        for o in prev_sel:
            o.select_set(False)
        obj.select_set(True)
        view_layer.objects.active = obj
        _bake(scene, "EMIT", 1, 1)
        w, h = out_img.size
        px = np.empty(w * h * 4, dtype=np.float32)
        out_img.pixels.foreach_get(px)
        px = px.reshape(h, w, 4)
        plain = float(np.median(px[2:6, 1:7, :3]))
        textured = float(np.median(px[2:6, 9:15, :3]))
    finally:
        bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.meshes.remove(mesh)
        for m in mats:
            bpy.data.materials.remove(m)
        bpy.data.images.remove(out_img)
        bpy.data.images.remove(tex_img)
        for o in prev_sel:
            try:
                o.select_set(True)
            except ReferenceError:
                pass
        view_layer.objects.active = prev_active
    # Undecoded, the texture's grey arrives one sRGB step brighter.
    brighter = float(to_srgb(np.array(plain)))
    decoded = abs(textured - plain) <= abs(textured - brighter)
    if not decoded:
        print(f"Papercraft: Cycles is not decoding sRGB textures here (plain {plain:.3f}, "
              f"texture {textured:.3f}); decoding them for the bake instead")
    return decoded


def linearize_textures(materials):
    """Point the bake's material copies at linear float copies of their sRGB
    image textures (decoded here, marked Non-Color so Cycles takes them as
    they are). Node groups are copied too, so the originals never change.
    Returns the datablocks to remove afterwards."""
    import numpy as np

    from .textures import from_srgb

    groups, images = {}, {}

    def linear_copy(img):
        if img.name in images:
            return images[img.name]
        cs = img.colorspace_settings
        if img.is_float or cs.is_data or "srgb" not in cs.name.lower():
            images[img.name] = None
            return None
        w, h = img.size
        if w == 0 or h == 0:
            images[img.name] = None
            return None
        px = np.empty(w * h * 4, dtype=np.float32)
        img.pixels.foreach_get(px)
        px = px.reshape(-1, 4)
        px[:, :3] = from_srgb(px[:, :3])
        lin = bpy.data.images.new(f"Papercraft Linear ({img.name})", w, h, alpha=True,
                                  float_buffer=True)
        lin.colorspace_settings.name = "Non-Color"
        lin.alpha_mode = img.alpha_mode
        lin.pixels.foreach_set(px.ravel())
        images[img.name] = lin
        return lin

    def fix(tree):
        for node in tree.nodes:
            if node.type == "GROUP" and node.node_tree is not None:
                original = node.node_tree
                if original.name not in groups:
                    groups[original.name] = original.copy()
                    fix(groups[original.name])
                node.node_tree = groups[original.name]
            elif node.type == "TEX_IMAGE" and node.image is not None:
                lin = linear_copy(node.image)
                if lin is not None:
                    node.image = lin

    for m in materials:
        if m is not None and m.node_tree is not None:
            fix(m.node_tree)
    return list(groups.values()), [i for i in images.values() if i is not None]


def page_of(items):
    return items[0][2] if items and len(items[0]) > 2 else None


def _page_mesh(src_obj, entry, items, name):
    src, res, lay = entry.src, entry.res, entry.lay
    inv = src_obj.matrix_world.inverted()
    verts, faces, mats, corners, page_uv = [], [], [], [], []
    for isl, f, _page in items:
        face = res.faces[f]
        base = len(verts)
        verts.extend(tuple(inv @ Vector(src.verts[v])) for v in face.loop)
        faces.append(list(range(base, base + len(face.loop))))
        mats.append(face.material)
        corners.append(face)
        for x, y in lay.face_points(res, isl, f):
            page_uv.extend((x / lay.page_w, y / lay.page_h))
    # Outside tabs on this page: bake the art of the face they cover, from
    # that face's real surface (so procedural/object coordinates match).
    for tab in lay.tabs:
        if not tab.outer or tab.partner is None:
            continue
        if lay.placements[tab.island].page != page_of(items):
            continue
        partner = res.faces[tab.partner[0]]
        corners3 = [Vector(src.verts[v]) for v in partner.loop]
        weights = list(tab.weights)
        page_pts = lay.tab_points(tab)
        if polygon_area(page_pts) < 0:  # wind like the face it covers
            weights, page_pts = weights[::-1], page_pts[::-1]
        base = len(verts)
        for ws in weights:
            p3 = sum((corners3[c] * w for c, w in ws), Vector())
            verts.append(tuple(inv @ p3))
        faces.append(list(range(base, base + len(weights))))
        mats.append(partner.material)
        corners.append(("interp", partner, weights))
        for x, y in page_pts:
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


_MODE_SET = {
    "EDIT_MESH": "EDIT",
    "PAINT_TEXTURE": "TEXTURE_PAINT",
    "PAINT_VERTEX": "VERTEX_PAINT",
    "PAINT_WEIGHT": "WEIGHT_PAINT",
    "SCULPT": "SCULPT",
}


def _baked_brightness(image):
    """Mean brightness of the baked (opaque) pixels, 0..1; -1 if none."""
    import numpy as np

    w, h = image.size
    px = np.empty(w * h * 4, dtype=np.float32)
    image.pixels.foreach_get(px)
    px = px.reshape(-1, 4)
    solid = px[px[:, 3] > 0.5]
    if len(solid) == 0:
        return -1.0
    return float(solid[:, :3].mean())


def bake_pages(context, src_obj, entry, dpi=200, bake_pass="AUTO", margin_mm=0.3,
               samples=None, progress=None, clip=None):
    """Return one Texture (data: URI PNG) per page, or None for empty pages.

    clip: per page polygons (page mm) to trim each page image to, so the
    pieces + bleed are in the PNG itself (no SVG clip path needed).

    The model's own materials are never modified: the bake runs on
    temporary copies (adding a bake target to a material and restoring its
    selected node can switch its active Material Output, and paint add-ons
    such as Paint System react to node changes)."""
    scene = context.scene
    res, lay = entry.res, entry.lay
    width = max(16, round(lay.page_w / 25.4 * dpi))
    height = max(16, round(lay.page_h / 25.4 * dpi))
    margin_px = max(1, round(margin_mm / 25.4 * dpi))
    passes = ["BASE", "EMIT", "COMBINED"] if bake_pass == "AUTO" else [bake_pass]

    per_page = {}
    for isl in res.islands:
        page = lay.placements[isl.index].page
        for f in isl.order:
            per_page.setdefault(page, []).append((isl, f, page))

    view_layer = context.view_layer
    prev_selected = [o.name for o in context.selected_objects]
    prev_active = view_layer.objects.active.name if view_layer.objects.active else None
    prev_mode = context.mode
    image_paint = scene.tool_settings.image_paint
    prev_canvas = image_paint.canvas
    if prev_mode != "OBJECT" and view_layer.objects.active is not None:
        bpy.ops.object.mode_set(mode="OBJECT")

    # Private copies of the model's materials (node groups and images are
    # shared, so everything shades exactly the same).
    copies = []
    for slot in src_obj.material_slots or [None]:
        m = slot.material if slot is not None else None
        if m is None or not m.use_nodes or m.node_tree is None:
            copies.append(_fallback_material(m))
        else:
            c = m.copy()
            c.name = f"Papercraft Bake ({m.name})"
            copies.append(c)

    # The model sits exactly where the bake copy is: rays leaving the copy
    # would hit its identical surface (streaky self-shadowing, "acne").
    # Hide it and every papercraft helper (layouts, rigs) from rendering.
    from . import targets as T

    hidden = []
    for o in scene.objects:
        if o == src_obj or o.papercraft.role in T.GENERATED:
            if not o.hide_render:
                hidden.append(o.name)
                o.hide_render = True

    results = []
    chosen = None
    fixed_groups, fixed_images = [], []
    bake_pages.textures_fixed = False
    try:
        decoded = textures_decode(context)
    except Exception as exc:  # the check must never stop a bake
        print(f"Papercraft: texture decode check skipped: {exc}")
        decoded = True
    if not decoded:
        fixed_groups, fixed_images = linearize_textures(copies)
        bake_pages.textures_fixed = bool(fixed_images)
    check_img = bpy.data.images.new("Papercraft Bake Check", 16, 16, alpha=True)
    check_obj, check_mat, check_bsdf, check_emit = _check_object(scene, check_img)
    srgb_steps = []
    try:
        for page in range(max(lay.page_count, 1)):
            items = per_page.get(page)
            if not items:
                results.append(None)
                continue
            if progress:
                progress(page)
            mesh = _page_mesh(src_obj, entry, items, f"Papercraft Bake {page}")
            for m in copies:
                mesh.materials.append(m)
            obj = bpy.data.objects.new("Papercraft Bake", mesh)
            obj.matrix_world = src_obj.matrix_world
            scene.collection.objects.link(obj)
            image = bpy.data.images.new(f"Papercraft Page {page + 1}", width, height, alpha=True)
            image.generated_color = (0.0, 0.0, 0.0, 0.0)
            try:
                for m in copies:
                    node = m.node_tree.nodes.new("ShaderNodeTexImage")
                    node.image = image
                    m.node_tree.nodes.active = node
                for o in context.selected_objects:
                    o.select_set(False)
                obj.select_set(True)
                check_obj.select_set(True)
                view_layer.objects.active = obj
                # Auto: base colour first; materials that colour through an
                # emission path (common in paint add-ons) bake black there.
                for candidate in ([chosen] if chosen else passes):
                    _check_shader(check_mat, check_bsdf, check_emit, candidate)
                    _bake(scene, candidate, samples, margin_px)
                    if bake_pass != "AUTO" or chosen or candidate == passes[-1]:
                        break
                    if _baked_brightness(image) > 0.02:
                        break
                chosen = chosen or candidate
                steps = _srgb_steps(check_img)
                srgb_steps.append(steps)
                mask = None
                if clip is not None and page < len(clip):
                    mask = polygon_mask(width, height, lay.page_w, lay.page_h, clip[page])
                png = _encode_pixels(image, max(width, height), srgb_steps=steps, alpha_mask=mask)
                uri = f"data:image/png;base64,{base64.b64encode(png).decode('ascii')}"
                results.append(Texture(uri, width, height, masked=mask is not None))
            finally:
                bpy.data.objects.remove(obj, do_unlink=True)
                bpy.data.meshes.remove(mesh)
                bpy.data.images.remove(image)
    finally:
        check_mesh = check_obj.data
        bpy.data.objects.remove(check_obj, do_unlink=True)
        bpy.data.meshes.remove(check_mesh)
        bpy.data.materials.remove(check_mat)
        bpy.data.images.remove(check_img)
        for m in copies:
            bpy.data.materials.remove(m)
        copies = []
        for grp in fixed_groups:
            bpy.data.node_groups.remove(grp)
        for img in fixed_images:
            bpy.data.images.remove(img)
        for name in hidden:
            o = bpy.data.objects.get(name)
            if o is not None:
                o.hide_render = False
        for m in copies:
            bpy.data.materials.remove(m)
        for o in context.selected_objects:
            o.select_set(False)
        for name in prev_selected:
            o = bpy.data.objects.get(name)
            if o is not None and o.name in view_layer.objects:
                o.select_set(True)
        if prev_active and prev_active in view_layer.objects:
            view_layer.objects.active = bpy.data.objects[prev_active]
            if prev_mode in _MODE_SET:
                try:
                    bpy.ops.object.mode_set(mode=_MODE_SET[prev_mode])
                except RuntimeError:
                    pass
        try:
            image_paint.canvas = prev_canvas
        except (ReferenceError, TypeError):
            pass
    bake_pages.last_pass = chosen
    bake_pages.srgb_steps = srgb_steps
    return results


def _bake(scene, pass_name, samples, margin_px):
    bake_type, pass_filter = PASSES.get(pass_name, PASSES["BASE"])
    n = samples if samples is not None else (32 if bake_type == "COMBINED" else 4)
    with _SceneState(scene, n):
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
            bpy.ops.object.bake(margin_type="EXTEND", **kwargs)
        except TypeError:
            bpy.ops.object.bake(**kwargs)
