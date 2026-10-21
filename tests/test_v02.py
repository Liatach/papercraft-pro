"""v0.2 features inside Blender: textures, line styles, colour cuts, editor."""
import os
import sys
import xml.dom.minidom

import bpy
from mathutils import Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import papercraft_unfold as addon  # noqa: E402
from papercraft_unfold import editor, live  # noqa: E402
from papercraft_unfold import targets as T  # noqa: E402
from papercraft_unfold.core.svg import export_pages  # noqa: E402
from papercraft_unfold.textures import textures_for  # noqa: E402

OUT = os.path.join(ROOT, "tests", "out")
os.makedirs(OUT, exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
addon.register()
scene = bpy.context.scene


def clear():
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)


def textured_material(name, image):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = image
    nt.links.new(tex.outputs["Color"], nt.nodes["Principled BSDF"].inputs["Base Color"])
    return mat


def unfold(obj, **settings):
    bpy.context.view_layer.objects.active = obj
    for k, v in settings.items():
        setattr(obj.papercraft, k, v)
    assert bpy.ops.papercraft.unfold() == {"FINISHED"}
    return live.cached(obj)


def export(entry, name, **kw):
    tex = textures_for(entry.src.images, 1024)
    uvs = [entry.src.uv_layers.get(n) for n in entry.src.image_uvs]
    docs = export_pages(entry.res, entry.lay, colors=entry.src.colors, textures=tex,
                        uvs=uvs, title=name, **kw)
    for n, d in enumerate(docs, 1):
        xml.dom.minidom.parseString(d)
        with open(f"{OUT}/{name}_p{n}.svg", "w") as fh:
            fh.write(d)
    return docs


# 1. Textures: generated image (pixel path) on a monkey, file PNG on a cube.
clear()
grid = bpy.data.images.new("grid", 512, 512)
grid.generated_type = "COLOR_GRID"
bpy.ops.mesh.primitive_monkey_add()
monkey = bpy.context.active_object
monkey.data.materials.append(textured_material("grid_mat", grid))
entry = unfold(monkey, pages=2)
docs = export(entry, "tex_monkey")
assert "<pattern" in docs[0] and "data:image/png;base64" in docs[0], "texture missing"
print("monkey textured: patterns", docs[0].count("<pattern"), "size kB", len(docs[0]) // 1024)

png_path = os.path.join(OUT, "checker.png")
img2 = bpy.data.images.new("checker", 256, 256)
img2.generated_type = "UV_GRID"
img2.filepath_raw = png_path
img2.file_format = "PNG"
img2.save()
img_file = bpy.data.images.load(png_path)
bpy.ops.mesh.primitive_cube_add(location=(4, 0, 0))
cube = bpy.context.active_object
cube.data.materials.append(textured_material("file_mat", img_file))
entry = unfold(cube, pages=1)
for style in ("DEFAULT", "MULTIPLY", "BURN", "NONE"):
    docs = export(entry, f"tex_cube_{style.lower()}", line_style=style, line_darkness=0.5)
assert "mix-blend-mode:color-burn" in open(f"{OUT}/tex_cube_burn_p1.svg").read()
assert "display:none" in open(f"{OUT}/tex_cube_none_p1.svg").read()
print("cube file texture + line styles ok")

# 2. Cut rules on a cube.
clear()
bpy.ops.mesh.primitive_cube_add()
cube = bpy.context.active_object
me = cube.data
# Vertex Paint style: byte colours on face corners.
col = me.color_attributes.new("paint", "BYTE_COLOR", "CORNER")
me.color_attributes.active_color = col
for p in me.polygons:
    up = abs(p.normal.z) > 0.5
    for li in p.loop_indices:
        col.data[li].color = (1, 0, 0, 1) if up else (0, 0, 1, 1)
cube.papercraft.cut_colors = True
entry = unfold(cube)
print("colour cuts -> pieces", len(entry.res.islands), "| rule cuts", entry.src.rule_cuts)
assert len(entry.res.islands) == 3, len(entry.res.islands)
cube.papercraft.cut_colors = False

for m in ("a", "b"):
    me.materials.append(bpy.data.materials.new(m))
me.polygons[0].material_index = 1
cube.papercraft.cut_materials = True
entry = unfold(cube)
print("material cuts -> pieces", len(entry.res.islands))
assert len(entry.res.islands) == 2
cube.papercraft.cut_materials = False

sharp = me.attributes.get("sharp_edge") or me.attributes.new("sharp_edge", "BOOLEAN", "EDGE")
for e in me.edges:
    sharp.data[e.index].value = True
cube.papercraft.cut_sharp = True
entry = unfold(cube)
print("sharp cuts -> pieces", len(entry.res.islands))
assert len(entry.res.islands) == 6
cube.papercraft.cut_sharp = False
for e in me.edges:
    sharp.data[e.index].value = False

# 3. Editor: pick + apply with synthetic rays.
entry = unfold(cube)
assert len(entry.res.islands) == 1
# Ray straight down onto the top face of the cube, near its +X edge.
p = editor.pick(cube, entry, Vector((0.95, 0.0, 5.0)), Vector((0, 0, -1)))
print("model pick:", p.action, p.key, "highlights", len(p.segments))
before = entry.res.edges[p.key].kind
assert p.action == ("cut" if before == "fold" else "join")
assert editor.apply_pick(cube, p)
entry = live.regenerate(scene, bpy.context.evaluated_depsgraph_get(), cube, force=True)
after = entry.res.edges[p.key].kind
print("edge", before, "->", after)
assert after != before

# Click again: it toggles back.
p2 = editor.pick(cube, entry, Vector((0.95, 0.0, 5.0)), Vector((0, 0, -1)))
assert p2.action != p.action, (p.action, p2.action)
editor.apply_pick(cube, p2)
entry = live.regenerate(scene, bpy.context.evaluated_depsgraph_get(), cube, force=True)
assert entry.res.edges[p.key].kind == before
print("toggled back ok:", p2.action)

# Layout pick: shoot down at the centre of a laid-out tab, flip it.
tab = entry.lay.tabs[0]
page = entry.lay.placements[tab.island].page
tp = entry.lay.tab_points(tab)
centre = Vector(entry.frame.to_world(page, (sum(x for x, _ in tp) / 4, sum(y for _, y in tp) / 4), 1.0))
pt = editor.pick(cube, entry, centre + Vector((0, 0, 5)), Vector((0, 0, -1)))
print("layout pick:", pt.action if pt else None)
assert pt is not None and pt.action == "flip"
side_before = (tab.face, tab.corner)
editor.apply_pick(cube, pt)
entry = live.regenerate(scene, bpy.context.evaluated_depsgraph_get(), cube, force=True)
moved = [t for t in entry.lay.tabs if t.number == tab.number][0]
print("tab side", side_before, "->", (moved.face, moved.corner))
assert (moved.face, moved.corner) != side_before

# Layout pick on a face edge -> cut it from the paper view.
isl = entry.res.islands[0]
f = isl.order[1]
pts = entry.lay.face_points(entry.res, isl, f)
cx = sum(x for x, _ in pts) / len(pts)
cy = sum(y for _, y in pts) / len(pts)
k_mid = ((pts[0][0] + pts[1][0]) / 2 * 0.8 + cx * 0.2, (pts[0][1] + pts[1][1]) / 2 * 0.8 + cy * 0.2)
w = Vector(entry.frame.to_world(entry.lay.placements[isl.index].page, k_mid, 1.0))
pf = editor.pick(cube, entry, w + Vector((0, 0, 5)), Vector((0, 0, -1)))
print("layout edge pick:", pf.action, pf.key, "segments", len(pf.segments))
assert pf.action in ("cut", "join")

addon.unregister()
print("V02 OK")
