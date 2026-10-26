"""v0.12: thick materials. Board presets, V-groove folds, mitred joins,
cut valley folds, creased-fold shortening and mirrored Back Marks pages."""
import math
import os
import re
import sys

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import papercraft_pro as addon  # noqa: E402
from papercraft_pro import live  # noqa: E402
from papercraft_pro.core import board as B  # noqa: E402
from papercraft_pro.core import geom as G  # noqa: E402
from papercraft_pro.core.unfold import KIND_CUT, KIND_FOLD  # noqa: E402
from papercraft_pro.ops import svg_documents, write_pdf  # noqa: E402

OUT = os.path.join(ROOT, "tests", "out")
os.makedirs(OUT, exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
addon.register()
ctx = bpy.context
scene = ctx.scene

# ------------------------------------------------------------ formulas
r90 = math.radians(90)
assert abs(B.groove_width(5, r90) - 10.0) < 1e-9          # 90 deg V in 5 mm: 10 mm wide
assert abs(B.mitre_inset(5, r90) - 5.0) < 1e-9            # 45 deg mitre: 5 mm back
assert abs(B.bend_shortening(1.5, r90) - 1.5 * (2 - math.pi / 2)) < 1e-9
assert B.groove_width(5, 0) == 0 and B.mitre_inset(5, 0) == 0
print("formulas: 90 deg in 5 mm flute -> 10 mm groove, 5 mm mitre; "
      f"1.5 mm radius crease shortens {B.bend_shortening(1.5, r90):.3f} mm")

# ------------------------------------------------------------ presets
bpy.ops.mesh.primitive_cube_add()
cube = ctx.active_object
ctx.view_layer.objects.active = cube
props = cube.papercraft
props.texture_mode = "WHITE"
props.scale_mode = "RATIO"
props.ratio = 40.0  # 80 mm cube
props.board = "FLUTE5"
assert (props.board_thickness, props.fold_style, props.join_style, props.cut_valleys,
        props.paper_thickness) == (5.0, "VGROOVE", "MITRE", True, 5.0)
assert bpy.ops.papercraft.unfold() == {"FINISHED"}
entry = live.cached(cube)
res, lay = entry.res, entry.lay
assert lay.tabs == [], "mitred board has no glue tabs"
print("preset Corrugated 5 mm: 5 mm, V-groove, mitre, valleys cut, no tabs")

# ------------------------------------------------------------ marks
marks = B.board_marks(res, lay, 5.0, "VGROOVE", "MITRE")[0]
folds = [k for k, i in res.edges.items() if i.kind == KIND_FOLD]
seams = [k for k, i in res.edges.items() if i.kind == KIND_CUT and len(i.uses) == 2]
assert len(marks["groove_centre"]) == len(folds)
assert len(marks["groove_back"]) == 2 * len(folds)
assert len(marks["bevel_back"]) == 2 * len(seams)
side = 80.0


def dist_point_line(p, a, b):
    d = G.sub2(b, a)
    return abs(d[0] * (p[1] - a[1]) - d[1] * (p[0] - a[0])) / G.len2(d)


for (a, b) in marks["groove_back"]:
    centre = min(marks["groove_centre"], key=lambda s: dist_point_line(a, *s) + dist_point_line(b, *s))
    assert abs(dist_point_line(a, *centre) - 5.0) < 1e-6
    assert abs(dist_point_line(b, *centre) - 5.0) < 1e-6
    assert abs(G.len2(G.sub2(b, a)) - side) < 1e-6
print(f"{len(folds)} folds -> grooves 5 mm each side of the fold line, full length")
for (a, b) in marks["bevel_back"]:
    assert abs(G.len2(G.sub2(b, a)) - side) < 1e-6
print(f"{len(seams)} seams -> mitre lines 5 mm in from the edge on both pieces")

# ------------------------------------------------------------ back pages
props.export_content = "BACK"
back = svg_documents(ctx, cube, entry)[0]
props.export_content = "CUT"
front_cut = svg_documents(ctx, cube, entry)[0]
assert '<g id="groove"' in back and '<g id="bevel"' in back and "BACK (mirrored)" in back
assert "<image" not in back and 'id="numbers"' not in back


def xs(doc, layer):
    body = doc.split(f'<g id="{layer}"')[1].split("</g>")[0]
    return sorted(round(float(x), 2) for x in re.findall(r"([-\d.]+),[-\d.]+", body))


mirrored = sorted(round(lay.page_w - x, 2) for x in xs(front_cut, "cut"))
assert mirrored == xs(back, "cut"), "back outline must be the mirror of the front"
props.export_content = "BACK"
path = write_pdf(ctx, cube, entry, os.path.join(OUT, "v12_cube.pdf"))
assert path.endswith("_back.pdf") and open(path, "rb").read().startswith(b"%PDF")
print("Back Marks: outline mirrored exactly, grooves + bevels, SVG and PDF")

# ------------------------------------------------------------ valleys cut
bpy.ops.mesh.primitive_torus_add(location=(5, 0, 0), major_segments=12, minor_segments=6)
torus = ctx.active_object
ctx.view_layer.objects.active = torus
tp = torus.papercraft
tp.texture_mode = "WHITE"
tp.board = "CARDSTOCK"
bpy.ops.papercraft.unfold()
e1 = live.cached(torus)
valley_folds = sum(1 for i in e1.res.edges.values() if i.kind == KIND_FOLD and i.angle < -0.02)
tp.board = "FLUTE3"
e2 = live.regenerate(scene, ctx.evaluated_depsgraph_get(), torus, force=True)
valley_after = sum(1 for i in e2.res.edges.values() if i.kind == KIND_FOLD and i.angle < -0.02)
print(f"torus valley folds: cardstock {valley_folds}, corrugated {valley_after}")
assert valley_folds > 0 and valley_after == 0
assert "inward fold" not in tp.info_message
tp.cut_valleys = False  # keep them: the panel must say they can't be grooved
e3 = live.regenerate(scene, ctx.evaluated_depsgraph_get(), torus, force=True)
assert "inward fold(s) can't be V-grooved" in tp.info_message, tp.info_message
m3 = B.board_marks(e3.res, e3.lay, 3.0, "VGROOVE", "MITRE")
assert sum(m["valley_folds"] for m in m3) > 0
assert all(len(m["groove_back"]) == 2 * len(m["groove_centre"]) for m in m3)
print("inward folds left uncut are flagged, never grooved")

# ------------------------------------------------------------ crease allowance
ctx.view_layer.objects.active = cube
props.export_content = "PRINT"
props.board = "CARDSTOCK"
e0 = live.regenerate(scene, ctx.evaluated_depsgraph_get(), cube, force=True)


def child_gaps(entry):
    out = {}
    for isl in entry.res.islands:
        for f in isl.order:
            if f == isl.root:
                continue
            p = isl.parent[f][0]
            cf = G.polygon_centroid(entry.lay.face_points(entry.res, isl, f))
            cp = G.polygon_centroid(entry.lay.face_points(entry.res, isl, p))
            out[(p, f)] = G.len2(G.sub2(cf, cp))
    return out


plain = child_gaps(e0)
props.board = "CARDBOARD"  # 1.5 mm, crease, 1.5 mm radius
assert props.fold_radius == 1.5 and props.join_style == "TABS"
e1 = live.regenerate(scene, ctx.evaluated_depsgraph_get(), cube, force=True)
creased = child_gaps(e1)
want = B.bend_shortening(1.5, r90)
for k, d in plain.items():
    assert abs((d - creased[k]) - want) < 1e-6, (d, creased[k], want)
print(f"Cardboard: every creased fold brings neighbouring faces {want:.3f} mm closer")
# Back to cardstock: exact model size again.
props.board = "CARDSTOCK"
e2 = live.regenerate(scene, ctx.evaluated_depsgraph_get(), cube, force=True)
assert all(abs(child_gaps(e2)[k] - d) < 1e-9 for k, d in plain.items())
print("Cardstock again: exact sizes restored")

addon.unregister()
print("V12 OK")
