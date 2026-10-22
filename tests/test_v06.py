"""v0.6: cut / crease-only export for cutting machines; clean Full Render bakes."""
import base64
import glob
import os
import re
import sys

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import papercraft_unfold as addon  # noqa: E402
from papercraft_unfold import live  # noqa: E402
from papercraft_unfold.core.geom import interior_point  # noqa: E402
from papercraft_unfold.ops import svg_documents  # noqa: E402

OUT = os.path.join(ROOT, "tests", "out", "v06")
os.makedirs(OUT, exist_ok=True)
for f in glob.glob(os.path.join(OUT, "*")):
    os.remove(f)
bpy.ops.wm.read_factory_settings(use_empty=True)
addon.register()
ctx = bpy.context
sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
sun.rotation_euler = (0.7, 0.3, 0.5)
ctx.scene.collection.objects.link(sun)

bpy.ops.mesh.primitive_cone_add(vertices=6)
cone = ctx.active_object
mat = bpy.data.materials.new("plain")
mat.use_nodes = True
cone.data.materials.append(mat)
assert bpy.ops.papercraft.unfold() == {"FINISHED"}
entry = live.cached(cone)
pc = cone.papercraft

# ------------------------------------------------ cutter exports
pc.dashed_folds = False
for content in ("CUT", "CREASE", "LINES"):
    pc.export_content = content
    assert bpy.ops.papercraft.export_svg(filepath=os.path.join(OUT, "cone.svg")) == {"FINISHED"}
files = sorted(os.path.basename(f) for f in glob.glob(os.path.join(OUT, "*.svg")))
print("files:", files)
assert files == ["cone_crease.svg", "cone_cut.svg", "cone_lines.svg"], files
cut = open(os.path.join(OUT, "cone_cut.svg")).read()
crease = open(os.path.join(OUT, "cone_crease.svg")).read()
both = open(os.path.join(OUT, "cone_lines.svg")).read()
for name, doc in (("cut", cut), ("crease", crease), ("lines", both)):
    for banned in ("<image", "<text", "<polygon", "clipPath", "stroke-dasharray:3", "stroke-opacity"):
        assert banned not in doc, (name, banned)
assert 'id="cut"' in cut and 'id="mountain"' not in cut and 'id="valley"' not in cut
assert 'id="cut"' not in crease and 'id="mountain"' in crease
assert "stroke:#ff0000" in both and "stroke:#000000" in both
n_cut = cut.count("<path")
n_fold = crease.count("<path")
print(f"cut paths {n_cut}, crease paths {n_fold}, combined {both.count('<path')}")
assert n_cut > 0 and n_fold > 0 and both.count("<path") == n_cut + n_fold
assert "stroke-dasharray:none" in crease  # solid creases for scoring

# ------------------------------------------------ print still works, model visibility restored
pc.export_content = "PRINT"
pc.texture_mode = "BAKE"
pc.bake_pass = "COMBINED"
pc.bake_dpi = 80
assert not cone.hide_render
doc = svg_documents(ctx, cone, entry)[0]
assert not cone.hide_render, "bake must restore the model's render visibility"
assert "<text" in doc and "<image" in doc

# Every face of a Full Render bake is one even shade (no streaks / acne).
uri = doc.split('href="data:image/png;base64,')[1].split('"')[0]
probe = os.path.join(OUT, "cone_page.png")
with open(probe, "wb") as fh:
    fh.write(base64.b64decode(uri))
img = bpy.data.images.load(probe)
W, H = img.size
px = img.pixels[:]
lay = entry.lay
worst = 0.0
for isl in entry.res.islands:
    for f in isl.order:
        pts = lay.face_points(entry.res, isl, f)
        cx, cy = interior_point(pts)
        vals = []
        for dx in range(-3, 4):
            for dy in range(-3, 4):
                x = int(cx / lay.page_w * W) + dx
                y = int(cy / lay.page_h * H) + dy
                i = (y * W + x) * 4
                vals.append(sum(px[i:i + 3]) / 3)
        spread = max(vals) - min(vals)
        worst = max(worst, spread)
print(f"Full Render bake: worst brightness spread inside a face {worst:.3f}")
assert worst < 0.05

addon.unregister()
print("V06 OK")
