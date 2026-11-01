"""v0.15: flaps on open edges (joining separately made parts) and page
arrangements; page and tab changes re-pack without unfolding again."""
import os
import re
import sys

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import papercraft_pro as addon  # noqa: E402
from papercraft_pro import build, editor, live  # noqa: E402
from papercraft_pro.core import geom as g  # noqa: E402
from papercraft_pro.core import layout as L  # noqa: E402
from papercraft_pro.core.svg import glue_footprints  # noqa: E402
from papercraft_pro.mesh_io import OPEN_ATTR  # noqa: E402
from papercraft_pro.ops import svg_documents, write_pages, write_pdf  # noqa: E402

OUT = os.path.join(ROOT, "tests", "out")
os.makedirs(OUT, exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
addon.register()
ctx = bpy.context
scene = ctx.scene


def two_halves():
    """A box made as two open halves (lower and upper), not joined: the
    four edges round the middle are doubled, open on both halves."""
    s = 1.0
    lo = [(-s, -s, -s), (s, -s, -s), (s, s, -s), (-s, s, -s)]
    mid = [(-s, -s, 0.0), (s, -s, 0.0), (s, s, 0.0), (-s, s, 0.0)]
    hi = [(-s, -s, s), (s, -s, s), (s, s, s), (-s, s, s)]
    verts = lo + mid + mid + hi  # 0-3 bottom, 4-7 lower rim, 8-11 upper rim, 12-15 top
    faces = [(3, 2, 1, 0)]  # bottom, facing down
    for i in range(4):
        j = (i + 1) % 4
        faces.append((i, j, 4 + j, 4 + i))  # lower sides
    for i in range(4):
        j = (i + 1) % 4
        faces.append((8 + i, 8 + j, 12 + j, 12 + i))  # upper sides
    faces.append((12, 13, 14, 15))  # top
    me = bpy.data.meshes.new("halves")
    me.from_pydata(verts, [], faces)
    me.update()
    ob = bpy.data.objects.new("Halves", me)
    scene.collection.objects.link(ob)
    return ob


def edge_index(me, a, b):
    for e in me.edges:
        if set(e.vertices) == {a, b}:
            return e.index
    raise KeyError((a, b))


# ------------------------------------------------------------ open flaps
box = two_halves()
ctx.view_layer.objects.active = box
props = box.papercraft
props.texture_mode = "WHITE"
props.number_mode = "FACES"
assert bpy.ops.papercraft.unfold() == {"FINISHED"}
entry = live.cached(box)
res, lay = entry.res, entry.lay
glued = res.glue_count
assert len(lay.tabs) == glued and not any(t.open for t in lay.tabs)
partners = L.match_open_edges(res)
assert len(partners) == 8, partners
for k, (other, _use) in partners.items():
    assert partners[other][0] == k
print(f"two halves: {len(res.islands)} pieces, {glued} glued seams, 4 rim edges paired")

# The tool: an open edge offers a flap.
rim = [(4, 5), (5, 6), (6, 7), (4, 7)]
assert all(editor.edge_action(entry, k) == "open" for k in rim)
first_res = res
for a, b in rim:
    p = editor.Pick("open", (a, b))
    assert editor.apply_pick(box, p)
entry = live.regenerate(scene, ctx.evaluated_depsgraph_get(), box, force=True)
assert entry.res is first_res, "a flap re-packs the same unfold"
res, lay = entry.res, entry.lay
flaps = [t for t in lay.tabs if t.open]
assert len(flaps) == 4 and len(lay.tabs) == glued + 4
attr = box.data.attributes[OPEN_ATTR]
assert sum(d.value for d in attr.data) == 4
numbers = sorted(t.number for t in flaps)
assert numbers == list(range(glued + 1, glued + 5)), numbers
for t in flaps:
    assert t.partner is not None and t.weights and not t.squashed
    face = res.faces[t.face]
    key = (face.loop[t.corner], face.loop[(t.corner + 1) % len(face.loop)])
    key = key if key[0] < key[1] else key[::-1]
    other = partners[key][0]
    assert res.edges[other].number == t.number, "both edges carry the number"
    # The two halves' walls continue straight on: a flat fold.
    assert abs(t.angle) < 1e-3, t.angle
    assert abs(g.polygon_area(t.points)) > 1.0
print(f"rim flaps: numbered {numbers[0]}..{numbers[-1]}, fitted to the other half")

foot = glue_footprints(res, lay)
assert len(foot) == len(lay.tabs), "every tab, flaps too, has a glue guide"
docs = svg_documents(ctx, box, entry)
sheets = "".join(docs[: lay.page_count])
for n in numbers:
    assert len(re.findall(rf">{n}<", sheets)) >= 2, f"number {n} on both edges"
write_pages(ctx, box, entry, os.path.join(OUT, "v15_halves.svg"))
pdf = write_pdf(ctx, box, entry, os.path.join(OUT, "v15_halves.pdf"))
assert os.path.getsize(pdf) > 1000
props.number_mode = "CLEAN"
docs = svg_documents(ctx, box, entry)
sheets = "".join(docs[: lay.page_count])
for n in numbers:
    assert len(re.findall(rf">{n}<", sheets)) == 2, f"number {n}: flap and scrap"
props.number_mode = "FACES"
print("exports: matching numbers on flap and partner edge (SVG and PDF), glue guides")

# The rig folds the flaps like any tab.
arm, folding = build.build_rig(ctx, box, entry)
names = {b.name for b in arm.data.bones}
assert sum(1 for n in names if n.startswith("T")) == len(lay.tabs)
print(f"rig: {len(arm.data.bones)} bones, flaps included")

# Shift on a flap that has a partner: glue on the outside.
t0 = flaps[0]
face = res.faces[t0.face]
k0 = (face.loop[t0.corner], face.loop[(t0.corner + 1) % len(face.loop)])
editor.apply_pick(box, editor.Pick("outer", k0))
entry = live.regenerate(scene, ctx.evaluated_depsgraph_get(), box, force=True)
assert sum(1 for t in entry.lay.tabs if t.open and t.outer) == 1
editor.apply_pick(box, editor.Pick("outer", k0))
# Clicking the flap again removes it.
editor.apply_pick(box, editor.Pick("open", k0))
entry = live.regenerate(scene, ctx.evaluated_depsgraph_get(), box, force=True)
assert sum(1 for t in entry.lay.tabs if t.open) == 3
assert entry.res.edges[tuple(sorted(k0))].number == 0
print("Shift: flap outside; click again: flap removed (and its number)")

# An open edge with nothing to meet: a plain flap, no number.
bpy.ops.mesh.primitive_grid_add(x_subdivisions=3, y_subdivisions=3, size=2.0)
grid = ctx.active_object
gp = grid.papercraft
gp.texture_mode = "WHITE"
assert bpy.ops.papercraft.unfold() == {"FINISHED"}
ge = live.cached(grid)
edge = next(k for k, i in ge.res.edges.items() if len(i.uses) == 1)
assert editor.edge_action(ge, edge) == "open"
editor.apply_pick(grid, editor.Pick("open", edge))
ge = live.regenerate(scene, ctx.evaluated_depsgraph_get(), grid, force=True)
(flap,) = [t for t in ge.lay.tabs if t.open]
assert flap.partner is None and flap.number == 0 and flap.angle == 0.0
assert abs(g.polygon_area(flap.points)) > 1.0
svg_documents(ctx, grid, ge)
build.build_rig(ctx, grid, ge)
print("open grid: a plain flap (nothing to match), exports and rigs fine")

# ------------------------------------------------------------ re-pack only
bpy.ops.mesh.primitive_monkey_add()
monkey = ctx.active_object
mp = monkey.papercraft
mp.texture_mode = "WHITE"
mp.pages = 3
assert bpy.ops.papercraft.unfold() == {"FINISHED"}
m_entry = live.cached(monkey)
m_res = m_entry.res
dg = ctx.evaluated_depsgraph_get()
for name, value in (("pages", 2), ("paper", "A3"), ("landscape", True), ("margin", 6.0),
                    ("spacing", 4.0), ("tab_height", 4.0), ("scale_mode", "HEIGHT"),
                    ("scale_mode", "FIT")):
    setattr(mp, name, value)
    m_entry = live.regenerate(scene, dg, monkey)
    assert m_entry.res is m_res, f"{name}: re-packed, not unfolded again"
mp.paper, mp.landscape, mp.margin, mp.spacing, mp.tab_height = "A4", False, 10.0, 3.0, 5.0
mp.pages = 3
m_entry = live.regenerate(scene, dg, monkey)
assert m_entry.res is m_res
mp.max_faces = 40
m_entry = live.regenerate(scene, dg, monkey)
assert m_entry.res is not m_res, "a cut setting unfolds again"
mp.max_faces = 0
m_entry = live.regenerate(scene, dg, monkey)
m_res = m_entry.res
assert live.get_or_build(ctx, monkey, force=True, full=True).res is not m_res, "Unfold button"
m_res = live.cached(monkey).res
# Bend allowance on a re-used unfold doesn't pile up.
mp.board = "CARDBOARD"
mp.fold_style = "CREASE"
mp.fold_radius = 2.0
a1 = live.regenerate(scene, dg, monkey)
shape1 = [list(p) for p in a1.res.islands[0].faces[a1.res.islands[0].root]]
mp.spacing = 3.5
a2 = live.regenerate(scene, dg, monkey)
assert a2.res is a1.res
shape2 = [list(p) for p in a2.res.islands[0].faces[a2.res.islands[0].root]]
assert shape1 == shape2
mp.board = "CARDSTOCK"
mp.fold_radius = 0.0
mp.spacing = 3.0
m_entry = live.regenerate(scene, dg, monkey)
print("page, scale and tab settings re-pack the same pieces; cut settings unfold again")

# With Live Update off, a layout setting still re-packs (once unfolded).
mp.live = False
live._state["pending"].clear()
mp.arrange = "EVEN"
assert monkey.name in live._state["pending"]
live._run()
assert live.cached(monkey).lay is not m_entry.lay
mp.arrange = "COMPACT"
live._run()
print("live off: changing the arrangement re-packs straight away")


# ------------------------------------------------------------ arrangements
def check_layout(res, lay):
    """No two pieces (with their tabs) overlap; all inside the margins."""
    by_page = {}
    for isl in res.islands:
        pl = lay.placements[isl.index]
        pts = [q for f in isl.faces for q in lay.face_points(res, isl, f)]
        pts += [q for t in lay.tabs if t.island == isl.index for q in lay.tab_points(t)]
        b = g.bbox(pts)
        assert b[0] >= lay.margin - 0.01 and b[1] >= lay.margin - 0.01
        assert b[2] <= lay.page_w - lay.margin + 0.01 and b[3] <= lay.page_h - lay.margin + 0.01, (
            mp.arrange, isl.index, b, lay.page_w, lay.page_h, lay.fits)
        by_page.setdefault(pl.page, []).append(b)
    for boxes in by_page.values():
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                ov = min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])
                assert ov[0] <= 0.01 or ov[1] <= 0.01, "pieces overlap"
    return by_page


def loads(res, lay):
    out = [0.0] * lay.page_count
    for isl in res.islands:
        out[lay.placements[isl.index].page] += sum(abs(res.faces[f].area) for f in isl.faces)
    return out


mp.scale_mode = "HEIGHT"
mp.model_size_mm = 100.0
mp.max_faces = 30
results = {}
for mode in L.ARRANGE_MODES:
    mp.arrange = mode
    e = live.regenerate(scene, dg, monkey)
    assert e.lay.fits, mode
    check_layout(e.res, e.lay)
    results[mode] = (e.lay.page_count, loads(e.res, e.lay), e.lay)
compact_pages, compact_load, _ = results["COMPACT"]
even_pages, even_load, even_lay = results["EVEN"]
assert compact_pages >= 2, "test needs several pages"
assert even_pages == compact_pages
spread = lambda ld: max(ld) / max(min(ld), 1e-9)  # noqa: E731
assert spread(even_load) <= spread(compact_load) + 1e-9, (even_load, compact_load)
print(f"Fewest Pages: {compact_pages} pages, load spread {spread(compact_load):.1f}x; "
      f"Spread Evenly: {even_pages} pages, {spread(even_load):.1f}x")

# Spread Evenly centres each page's pieces.
mp.arrange = "EVEN"
e = live.regenerate(scene, dg, monkey)
for page in range(e.lay.page_count):
    pts = [q for isl in e.res.islands if e.lay.placements[isl.index].page == page
           for f in isl.faces for q in e.lay.face_points(e.res, isl, f)]
    b = g.bbox(pts)
    cx = (b[0] + b[2]) / 2
    assert abs(cx - e.lay.page_w / 2) < e.lay.page_w * 0.2, (page, cx)

# Neighbours Together: pieces in glue order, never back to an earlier page.
mp.arrange = "NEIGHBOURS"
e = live.regenerate(scene, dg, monkey)
order = L.neighbour_order(e.res)
assert sorted(order) == list(range(len(e.res.islands)))
pages_seq = [e.lay.placements[i].page for i in order]
assert pages_seq == sorted(pages_seq), pages_seq
island_of = {f: isl.index for isl in e.res.islands for f in isl.faces}
linked = 0
for n, i in enumerate(order[1:], 1):
    earlier = set(order[:n])
    nbrs = {island_of[u[0]] for info in e.res.edges.values()
            if info.kind == "cut" and len(info.uses) == 2
            for u in info.uses
            if island_of[info.uses[0][0]] == i or island_of[info.uses[1][0]] == i}
    linked += bool(nbrs & earlier)
assert linked >= 0.85 * (len(order) - 1), (linked, len(order))  # eyes are separate
print(f"Neighbours Together: {e.lay.page_count} pages; {linked}/{len(order) - 1} pieces "
      "follow one they glue to")

# Fit to Pages works with every arrangement.
mp.scale_mode = "FIT"
mp.pages = 2
for mode in L.ARRANGE_MODES:
    mp.arrange = mode
    e = live.regenerate(scene, dg, monkey)
    assert e.lay.fits and e.lay.page_count <= 2, (mode, e.lay.page_count)
    check_layout(e.res, e.lay)
print("Fit to 2 pages: fits in all three arrangements")

addon.unregister()
print("v0.15 OK")
