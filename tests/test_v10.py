"""v0.10: native PDF export; baked pages carry their own trim + bleed."""
import os
import re
import shutil
import subprocess
import sys
import zlib

import bpy
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import papercraft_pro as addon  # noqa: E402
from papercraft_pro import live  # noqa: E402
from papercraft_pro.core import geom as G  # noqa: E402
from papercraft_pro.ops import write_pdf  # noqa: E402

OUT = os.path.join(ROOT, "tests", "out")
os.makedirs(OUT, exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
addon.register()
ctx = bpy.context

bpy.ops.mesh.primitive_cube_add()
cube = ctx.active_object
S = 64
img = bpy.data.images.new("q", S, S)
px = np.zeros((S, S, 4), np.float32)
px[..., 3] = 1
px[..., 0] = 200 / 255
px[..., 1] = 30 / 255
px[..., 2] = 30 / 255
img.pixels.foreach_set(px.ravel())
img.pack()
mat = bpy.data.materials.new("m")
mat.use_nodes = True
tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
tex.image = img
mat.node_tree.links.new(tex.outputs[0], mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"])
cube.data.materials.append(mat)
ctx.view_layer.objects.active = cube
props = cube.papercraft
props.bake_dpi = 150
props.pages = 2
assert bpy.ops.papercraft.unfold() == {"FINISHED"}
entry = live.cached(cube)
pages = max(entry.lay.page_count, 1)


def pdf_pages(path):
    with open(path, "rb") as fh:
        data = fh.read()
    assert data.startswith(b"%PDF-1.4") and data.rstrip().endswith(b"%%EOF")
    return data, len(re.findall(rb"/Type /Page\b", data))


renders = {}
for name, content, mode in (("art", "ART_CUT", "BAKE"), ("print", "PRINT", "IMAGE"),
                            ("print_flat", "PRINT", "COLOR"), ("cut", "CUT", "BAKE")):
    props.export_content, props.texture_mode = content, mode
    path = write_pdf(ctx, cube, entry, os.path.join(OUT, f"v10_{name}.pdf"))
    data, n = pdf_pages(path)
    print(f"{os.path.basename(path)}: {n} page(s), {len(data)} bytes")
    assert n == pages
    if content == "CUT":
        assert b"/Im" not in data, "cut file carries no art"
    renders[name] = path

# Text is real text, the page is A4-ish size in points.
data, _ = pdf_pages(renders["print"])
assert b"/BaseFont /Helvetica" in data
w_pt, h_pt = [float(v) for v in re.search(rb"/MediaBox \[0 0 ([\d.]+) ([\d.]+)\]", data).groups()]
assert abs(w_pt - entry.lay.page_w * 72 / 25.4) < 0.1 and abs(h_pt - entry.lay.page_h * 72 / 25.4) < 0.1
print(f"page {w_pt:.1f} x {h_pt:.1f} pt = {entry.lay.page_w:g} x {entry.lay.page_h:g} mm")

# Render with Poppler (what Krita and many print tools use), if available.
if shutil.which("pdftoppm"):
    dpi = 150
    for name in ("art", "print"):
        base = os.path.join(OUT, f"v10_{name}_render")
        subprocess.run(["pdftoppm", "-r", str(dpi), "-png", "-f", "1", "-l", "1",
                        renders[name], base], check=True)
        png = base + ("-1.png" if pages < 10 else "-01.png")
        im = bpy.data.images.load(png)
        W, H = im.size
        a = np.empty(W * H * 4, np.float32)
        im.pixels.foreach_get(a)
        a = a.reshape(H, W, 4)  # rows bottom-up = page y up, like layout mm

        def rgb_at(x_mm, y_mm):
            return a[int(y_mm / entry.lay.page_h * H), int(x_mm / entry.lay.page_w * W), :3] * 255

        isl = next(i for i in entry.res.islands if entry.lay.placements[i.index].page == 0)
        f = isl.order[0]
        c = G.polygon_centroid(entry.lay.face_points(entry.res, isl, f))
        col = rgb_at(*c)
        print(f"{name}: face centre prints {np.round(col).astype(int)} (texture 200,30,30)")
        assert abs(col[0] - 200) < 12 and col[1] < 50 and col[2] < 50
        if name == "art":
            for t in entry.lay.tabs:
                if entry.lay.placements[t.island].page != 0:
                    continue
                tp = entry.lay.tab_points(t)
                tc = G.polygon_centroid(tp)
                col = rgb_at(*tc)
                assert col[0] > 150 and col[1] < 80, ("tab not carrying art", col)
            print("art: tabs carry the art in the PDF render")
else:
    print("pdftoppm not found: skipped render check")

addon.unregister()
print("V10 OK")
