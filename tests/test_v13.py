"""v0.13: Set Up Scene and render looks (from Pop-Up Pro); glue guides."""
import os
import re
import sys

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import papercraft_pro as addon  # noqa: E402
from papercraft_pro import editor, live  # noqa: E402
from papercraft_pro import scene_setup as S  # noqa: E402
from papercraft_pro import targets as T  # noqa: E402
from papercraft_pro.core import geom as G  # noqa: E402
from papercraft_pro.core.svg import glue_footprints  # noqa: E402
from papercraft_pro.ops import svg_documents, write_pdf  # noqa: E402

OUT = os.path.join(ROOT, "tests", "out")
os.makedirs(OUT, exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
addon.register()
ctx = bpy.context
scene = ctx.scene

bpy.ops.mesh.primitive_cube_add(location=(2, 1, 0.5), scale=(1.5, 1, 0.7))
cube = ctx.active_object
mat = bpy.data.materials.new("art")
mat.use_nodes = True
cube.data.materials.append(mat)
ctx.view_layer.objects.active = cube
cube.papercraft.texture_mode = "WHITE"
assert bpy.ops.papercraft.unfold() == {"FINISHED"}
entry = live.cached(cube)
ntabs = len(entry.lay.tabs)

# ------------------------------------------------------------ glue footprints
island_of = {f: isl for isl in entry.res.islands for f in isl.faces}


def inside(poly, p, eps=1e-6):
    n = len(poly)
    area = sum(poly[k][0] * poly[(k + 1) % n][1] - poly[(k + 1) % n][0] * poly[k][1] for k in range(n))
    sgn = 1 if area > 0 else -1
    for k in range(n):
        a, b = poly[k], poly[(k + 1) % n]
        if sgn * ((b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])) < -eps:
            return False
    return True


feet = glue_footprints(entry.res, entry.lay)
assert len(feet) == ntabs
for (page, (outer, pts)), tab in zip(feet, entry.lay.tabs):
    partner = entry.res.faces[tab.partner[0]]
    ppts = entry.lay.face_points(entry.res, island_of[partner.index], partner.index)
    assert all(inside(ppts, p) for p in pts), "glue footprint outside the face it glues to"
    tp = entry.lay.tab_points(tab)
    assert abs(abs(G.polygon_area(pts)) - abs(G.polygon_area(tp))) < 1e-6
print(f"{ntabs} glue footprints: each lies on its partner face, same size as its tab")

# ------------------------------------------------------------ where they print
props = cube.papercraft


def glue_count(doc):
    if '<g id="glue"' not in doc:
        return 0
    return doc.split('<g id="glue"')[1].split("</g>")[0].count("<polygon")


props.export_content = "PRINT"
front = svg_documents(ctx, cube, entry)
props.export_content = "BACK"
back = svg_documents(ctx, cube, entry)
assert sum(map(glue_count, front)) == 0, "inside guides must not print on the art side"
assert sum(map(glue_count, back)) == ntabs
props.export_content = "PRINT"
props.glue_guides_front = True
assert sum(map(glue_count, svg_documents(ctx, cube, entry))) == ntabs
props.glue_guides_front = False
props.glue_guides = False
props.export_content = "BACK"
assert sum(map(glue_count, svg_documents(ctx, cube, entry))) == 0
props.glue_guides = True
print("inside guides: Back Marks only (front on request); off when switched off")

# An outside tab's guide prints on the front, under the tab.
t0 = entry.lay.tabs[0]
f0 = entry.res.faces[t0.face]
key = tuple(sorted((f0.loop[t0.corner], f0.loop[(t0.corner + 1) % len(f0.loop)])))
assert editor.apply_pick(cube, editor.Pick("outer", key))
entry = live.regenerate(scene, ctx.evaluated_depsgraph_get(), cube, force=True)
props.export_content = "PRINT"
assert sum(map(glue_count, svg_documents(ctx, cube, entry))) == 1
# The outside tab's guide sits inside the tab's footprint with a margin, so
# the tab (and its art) hides it completely once glued.
outer_tab = next(t for t in entry.lay.tabs if t.outer)
k = entry.lay.tabs.index(outer_tab)
small = [p for p in glue_footprints(entry.res, entry.lay)][k][1][1]
outer_tab.outer = False
full = [p for p in glue_footprints(entry.res, entry.lay)][k][1][1]
outer_tab.outer = True


def edge_dist(poly, q):
    best = 1e9
    for i in range(len(poly)):
        a, b = poly[i], poly[(i + 1) % len(poly)]
        d = G.sub2(b, a)
        ln = G.len2(d)
        t = max(0.0, min(1.0, G.dot2(G.sub2(q, a), d) / (ln * ln)))
        best = min(best, G.len2(G.sub2(q, G.add2(a, G.mul2(d, t)))))
    return best


depth = max(edge_dist([full[0], full[-1]], q) for q in full)
want = min(1.0, 0.2 * depth)
assert all(inside(full, q) for q in small)
margin = min(edge_dist(full, q) for q in small)
print(f"outside guide: {margin:.2f} mm inside the tab on every side (tab {depth:.1f} mm deep)")
assert margin > want - 1e-6
assert abs(G.polygon_area(small)) < abs(G.polygon_area(full))
props.export_content = "BACK"
assert sum(map(glue_count, svg_documents(ctx, cube, entry))) == ntabs - 1
path = write_pdf(ctx, cube, entry, os.path.join(OUT, "v13_glue.pdf"))
data = open(path, "rb").read()
assert data.startswith(b"%PDF") and b"/ca 0.06" in data
print("outside tab: its guide on the front; Back Marks PDF carries the rest")

# ------------------------------------------------------------ scene setup
other = bpy.data.worlds.new("Mine")
scene.world = other
assert bpy.ops.papercraft.setup_scene("EXEC_DEFAULT", look="SHOWCASE", grain=True) == {"FINISHED"}
cam = scene.camera
assert cam is not None and cam.name == S.CAMERA
corners = [cube.matrix_world @ Vector(c) for c in cube.bound_box]
for c in corners:
    v = world_to_camera_view(scene, cam, c)
    assert 0.0 < v.x < 1.0 and 0.0 < v.y < 1.0 and v.z > 0, f"model not framed: {v}"
assert scene.world.name == S.SHOW
assert bpy.data.objects[S.SUN].hide_render and not bpy.data.objects[S.KEY].hide_render
assert scene.view_settings.view_transform == "Standard"
assert scene.eevee.taa_render_samples >= 64
print(f"Set Up Scene: camera frames the model, Showcase look, engine {scene.render.engine}")

# Paper grain: on the model's material, bump only, removable.
nt = mat.node_tree
bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
grain = nt.nodes.get(S.GRAIN)
assert grain is not None and bsdf.inputs["Normal"].links[0].from_node == grain
assert not bsdf.inputs["Base Color"].is_linked, "grain must not touch colour"
mpu = entry.lay.scale
assert abs(grain.inputs["Fibres per Unit"].default_value / (mpu / 0.25) - 1) < 1e-5
S.paper_texture(cube, on=False)
assert nt.nodes.get(S.GRAIN) is None and not bsdf.inputs["Normal"].is_linked
# A material with its own bump is left alone.
own = nt.nodes.new("ShaderNodeBump")
nt.links.new(own.outputs["Normal"], bsdf.inputs["Normal"])
assert S.paper_texture(cube, on=True, mm_per_unit=mpu) == 0
assert bsdf.inputs["Normal"].links[0].from_node == own
print("paper grain: bump only, sized to the print scale, removable, own bumps untouched")

# Looks.
for look, world, sun_r, key_r in (("CLEAN", S.WHITE, True, True), ("DAWN", S.DAWN, False, True),
                                  ("STUDIO", S.STUDIO, True, False)):
    bpy.ops.papercraft.render_look(look=look)
    assert scene.world.name == world, (look, scene.world.name)
    assert bpy.data.objects[S.SUN].hide_render == sun_r
    assert bpy.data.objects[S.KEY].hide_render == key_r
bpy.ops.papercraft.render_look(look="OWN")
assert scene.world == other, "Your Own puts your world back"
assert bpy.data.objects[S.SUN].hide_render and bpy.data.objects[S.KEY].hide_render
print("looks: Clean / Dawn / Studio worlds and lights; Your Own restores your world")

# Show in renders.
bpy.ops.papercraft.build_rig()
S.show_in_renders(cube, layout=False, rig=True, source=False)
assert T.output(cube, T.ROLE_LAYOUT).hide_render and cube.hide_render
assert not T.output(cube, T.ROLE_FOLDING).hide_render
assert bpy.ops.papercraft.frame_camera(frame="RIG") == {"FINISHED"}
cube.hide_render = False

addon.unregister()
print("V13 OK")
