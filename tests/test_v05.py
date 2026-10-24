"""v0.5: outside tabs (printed with the art they cover) and tab fold sides."""
import base64
import io
import os
import sys

import bpy
from mathutils import Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import papercraft_pro as addon  # noqa: E402
from papercraft_pro import editor, live  # noqa: E402
from papercraft_pro import targets as T  # noqa: E402
from papercraft_pro.core import unfold as U  # noqa: E402
from papercraft_pro.ops import svg_documents  # noqa: E402

OUT = os.path.join(ROOT, "tests", "out")
os.makedirs(OUT, exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
addon.register()
ctx = bpy.context
scene = ctx.scene


def regen(obj):
    return live.regenerate(scene, ctx.evaluated_depsgraph_get(), obj, force=True)


# A cube with a different flat colour per face, painted into an image
# through its UVs, so we can tell which face's art lands where.
bpy.ops.mesh.primitive_cube_add()
cube = ctx.active_object
me = cube.data
size = 256
img = bpy.data.images.new("faces", size, size)
pixels = [0.0] * (size * size * 4)
palette = [(1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 1, 0), (1, 0, 1), (0, 1, 1)]
uv = me.uv_layers.active.data
for p in me.polygons:
    col = palette[p.index]
    us = [uv[li].uv for li in p.loop_indices]
    x0, x1 = int(min(u.x for u in us) * size), int(max(u.x for u in us) * size)
    y0, y1 = int(min(u.y for u in us) * size), int(max(u.y for u in us) * size)
    for y in range(max(0, y0), min(size, y1)):
        for x in range(max(0, x0), min(size, x1)):
            i = (y * size + x) * 4
            pixels[i:i + 4] = [col[0], col[1], col[2], 1.0]
img.pixels.foreach_set(pixels)
img.pack()
mat = bpy.data.materials.new("faces")
mat.use_nodes = True
tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
tex.image = img
tex.interpolation = "Closest"
mat.node_tree.links.new(tex.outputs["Color"], mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"])
me.materials.append(mat)

ctx.view_layer.objects.active = cube
cube.papercraft.bake_dpi = 100
assert bpy.ops.papercraft.unfold() == {"FINISHED"}
entry = live.cached(cube)
tab = entry.lay.tabs[0]
assert not tab.outer and tab.partner is not None

# Shift+click the tab on the layout (simulated ray) -> outside.
page = entry.lay.placements[tab.island].page
tp = entry.lay.tab_points(tab)
centre = Vector(entry.frame.to_world(page, (sum(x for x, _ in tp) / len(tp),
                                            sum(y for _, y in tp) / len(tp)), 1.0))
p = editor.pick(cube, entry, centre + Vector((0, 0, 5)), Vector((0, 0, -1)))
assert p is not None and p.action == "flip"
p.action = "outer"
assert editor.apply_pick(cube, p)
entry = regen(cube)
outer = [t for t in entry.lay.tabs if t.outer]
assert len(outer) == 1, "the clicked tab should now be outside"
tab = outer[0]
partner = entry.res.faces[tab.partner[0]]

# The tab's hinge corners are the partner's corners: their UVs must match.
uvs = entry.src.uv_layers[entry.src.render_uv][partner.source]
puv = [uvs[c] for c in partner.corners]


def interp(ws):
    return (sum(puv[c][0] * w for c, w in ws), sum(puv[c][1] * w for c, w in ws))


face = entry.res.faces[tab.face]
a_v, b_v = face.loop[tab.corner], face.loop[(tab.corner + 1) % len(face.loop)]
for end, vid in ((0, a_v), (-1, b_v)):
    want = puv[partner.loop.index(vid)]
    got = interp(tab.weights[end])
    assert abs(got[0] - want[0]) < 1e-4 and abs(got[1] - want[1]) < 1e-4, (got, want)
print("outside tab: hinge corners map onto the covered face's UVs")

# Layout preview: the outside tab polygon uses the model's material.
lay_obj = T.output(cube, T.ROLE_LAYOUT)
tab_poly = [i for i, (kind, ref) in enumerate(entry.poly_map) if kind == "tab"
            and entry.lay.tabs[ref].outer][0]
slot = lay_obj.data.polygons[tab_poly].material_index
assert lay_obj.data.materials[slot] == mat, lay_obj.data.materials[slot].name
print("layout preview: outside tab shows the model's material")

# Image-mode SVG: the tab is filled with texture patterns.
cube.papercraft.texture_mode = "IMAGE"
docs = svg_documents(ctx, cube, entry)
with open(f"{OUT}/outer_tab_image.svg", "w") as fh:
    fh.write(docs[0])

# Baked SVG: sample the baked page at the tab centre: it must carry the
# covered face's colour (not empty paper, not the tab's own face colour).
cube.papercraft.texture_mode = "BAKE"
docs = svg_documents(ctx, cube, entry)
with open(f"{OUT}/outer_tab_baked.svg", "w") as fh:
    fh.write(docs[0])
uri = docs[0].split('href="data:image/png;base64,')[1].split('"')[0]
png = bpy.data.images.load(f"{OUT}/_probe.png") if False else None
probe = os.path.join(OUT, "outer_tab_page.png")
with open(probe, "wb") as fh:
    fh.write(base64.b64decode(uri))
page_img = bpy.data.images.load(probe)
W, H = page_img.size
tc = (sum(x for x, _ in entry.lay.tab_points(tab)) / len(tab.points),
      sum(y for _, y in entry.lay.tab_points(tab)) / len(tab.points))
px = int(tc[0] / entry.lay.page_w * W)
py = int(tc[1] / entry.lay.page_h * H)
i = (py * W + px) * 4
rgba = page_img.pixels[i:i + 4]
want = palette[partner.source]
print("baked page at outside tab:", [round(c, 2) for c in rgba], "covered face colour", want)
assert rgba[3] > 0.9, "outside tab area was not baked"
assert all(abs(rgba[k] - want[k]) < 0.25 for k in range(3)), "tab shows the wrong art"
assert "clip-path" not in docs[0], "the page image is trimmed itself: no clip path"

# Rig still folds exactly with an outside tab (and its material carries over).
assert bpy.ops.papercraft.build_rig() == {"FINISHED"}
folding = T.output(cube, T.ROLE_FOLDING)
assert mat in list(folding.data.materials)

# Toggle back inside.
p = editor.pick(cube, entry, centre + Vector((0, 0, 5)), Vector((0, 0, -1)))
p.action = "outer"
editor.apply_pick(cube, p)
entry = regen(cube)
assert not any(t.outer for t in entry.lay.tabs)
print("Shift+click again: back inside")

# Tab hinges follow the fold: valley edges get valley-folded tabs.
bpy.ops.mesh.primitive_monkey_add(location=(6, 0, 0))
monkey = ctx.active_object
ctx.view_layer.objects.active = monkey
monkey.papercraft.texture_mode = "WHITE"
bpy.ops.papercraft.unfold()
e2 = live.cached(monkey)
doc = svg_documents(ctx, monkey, e2)
valley_tabs = sum(1 for t in e2.lay.tabs if t.angle < -1e-6)
import math  # noqa: E402

min_fold = math.radians(monkey.papercraft.fold_min_angle)
valley_folds = 0
seen = set()
for isl in e2.res.islands:
    for f in isl.order:
        fc = e2.res.faces[f]
        for k in range(len(fc.loop)):
            key = U.edge_key(fc.loop[k], fc.loop[(k + 1) % len(fc.loop)])
            info = e2.res.edges[key]
            if info.kind == "fold" and key not in seen:
                seen.add(key)
                if info.angle <= -min_fold:
                    valley_folds += 1
drawn = sum(d.split('<g id="valley"')[1].split("</g>")[0].count("<path") for d in doc)
print(f"valley lines drawn {drawn} = valley folds {valley_folds} + valley tab hinges {valley_tabs}")
assert drawn == valley_folds + valley_tabs and valley_tabs > 0

addon.unregister()
print("V05 OK")
