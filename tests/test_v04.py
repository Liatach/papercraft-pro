"""v0.4: cutting order (Auto / rescue of tiny pieces) and tab rules.

Runs inside Blender (bpy) to get Suzanne and a torus; the checks themselves
are on the pure-Python core."""
import math
import os
import sys

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import papercraft_unfold as addon  # noqa: E402
from papercraft_unfold import live  # noqa: E402
from papercraft_unfold import targets as T  # noqa: E402
from papercraft_unfold.core import geom as g  # noqa: E402
from papercraft_unfold.core import layout as L  # noqa: E402
from papercraft_unfold.core import unfold as U  # noqa: E402


def mesh_data(obj):
    me = obj.data
    return [tuple(obj.matrix_world @ v.co) for v in me.vertices], [list(p.vertices) for p in me.polygons]


def inside(poly, region, tol_mm=1e-3):
    """Every tab corner on the inner side of each of the partner face's
    edges (within a micron)."""
    n = len(region)
    for p in poly:
        for k in range(n):
            a, b = region[k], region[(k + 1) % n]
            dist = g.cross2(g.sub2(b, a), g.sub2(p, a)) / max(g.len2(g.sub2(b, a)), 1e-12)
            if dist < -tol_mm:
                return False
    return True


bpy.ops.wm.read_factory_settings(use_empty=True)
addon.register()
bpy.ops.mesh.primitive_monkey_add()
monkey = bpy.context.active_object
bpy.ops.mesh.primitive_torus_add(major_segments=48, minor_segments=12, location=(6, 0, 0))
torus = bpy.context.active_object

# ---------------------------------------------------------- cutting order
mv, mf = mesh_data(monkey)
once = U.unfold(mv, mf, priority="flat", rescue_passes=0)
rescued = U.unfold(mv, mf, priority="long")
auto = U.unfold(mv, mf)
print(f"Suzanne pieces: flat/no rescue {len(once.islands)}, long+rescue {len(rescued.islands)}, "
      f"auto {len(auto.islands)} ({auto.priority})")
assert len(rescued.islands) < len(once.islands)
assert len(auto.islands) <= len(once.islands)

tv, tf = mesh_data(torus)
t_auto = U.unfold(tv, tf)
t_long = U.unfold(tv, tf, priority="long")
print(f"torus: auto -> {t_auto.priority} {len(t_auto.islands)} pieces "
      f"(tight seams {U.tight_seams(t_auto)} vs comb {U.tight_seams(t_long)})")
assert t_auto.priority == "flat", "Auto should avoid the comb-shaped torus"
assert 1 < len(t_auto.islands) <= 12

# ---------------------------------------------------------- tabs
for name, res in (("Suzanne", auto), ("torus", t_auto)):
    lay = L.compute_layout(res, scale_mode="FIT", max_pages=2, tab_height=5)
    S = lay.scale
    island_of = {f: i.index for i in res.islands for f in i.faces}
    by_key = {}
    for tab in lay.tabs:
        face = res.faces[tab.face]
        k = tab.corner
        key = U.edge_key(face.loop[k], face.loop[(k + 1) % len(face.loop)])
        by_key[key] = tab
        other = [u for u in res.edges[key].uses if u != (tab.face, tab.corner)][0]
        pts = [(x * S, y * S) for x, y in res.islands[tab.island].faces[tab.face]]
        a, b = pts[k], pts[(k + 1) % len(pts)]
        region, ko = L._partner_region(res, S, other, a, b)
        assert inside(tab.points, region), f"{name}: tab {tab.number} sticks out past its face"
        da, db = g.len2(g.sub2(tab.points[0], a)), g.len2(g.sub2(tab.points[-1], b))
        if da > 1e-6 or db > 1e-6:
            print("END MISMATCH", name, tab.number, round(da, 6), round(db, 6), len(tab.points), g.len2(g.sub2(b, a)))
            bad_ends = True
    chains = L._glue_chains(
        res, [k for k, e in res.edges.items() if e.kind == "cut" and len(e.uses) == 2], island_of
    )
    multi = [c for c in chains if len(c) > 1]
    one_sided = 0
    for chain in multi:
        sides = set()
        for key, (p, q) in chain:
            tab = by_key.get(key)
            if tab is None:
                continue
            loop = res.faces[tab.face].loop
            sides.add(loop[tab.corner] == p and loop[(tab.corner + 1) % len(loop)] == q)
        one_sided += len(sides) <= 1
    share = one_sided / max(1, len(multi))
    print(f"{name}: {len(lay.tabs)} tabs all inside their faces; "
          f"{one_sided}/{len(multi)} seams have every tab on one side")
    assert share >= 0.75, share
    assert not globals().get("bad_ends"), "tab outline must start/end on the hinge"

# ---------------------------------------------------------- rig targets tidy
bpy.context.view_layer.objects.active = torus
bpy.ops.papercraft.unfold()
bpy.ops.papercraft.build_rig()
targets = T.outputs(torus, T.ROLE_TARGET)
colls = {c.name for t in targets for c in t.users_collection}
print(f"{len(targets)} rig targets in {colls}")
assert len(colls) == 1 and next(iter(colls)).endswith("Rig Targets")
bpy.ops.papercraft.build_rig()  # rebuild reuses the sub-collection
assert len({c.name for t in T.outputs(torus, T.ROLE_TARGET) for c in t.users_collection}) == 1

addon.unregister()
print("V04 OK")
