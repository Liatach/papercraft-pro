"""v0.16: cuts and joins wait for Apply Cuts (Live Layout off), and applying
them keeps the pieces that weren't touched."""
import os
import sys
from types import SimpleNamespace

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import papercraft_pro as addon  # noqa: E402
from papercraft_pro import editor, live  # noqa: E402
from papercraft_pro.core.unfold import KIND_CUT, KIND_FOLD, unfold  # noqa: E402

bpy.ops.wm.read_factory_settings(use_empty=True)
addon.register()
ctx = bpy.context
scene = ctx.scene

bpy.ops.mesh.primitive_monkey_add()
monkey = ctx.active_object
props = monkey.papercraft
assert not props.live, "Live Layout is off by default"
assert bpy.ops.papercraft.unfold() == {"FINISHED"}
entry = live.cached(monkey)
res0 = entry.res
assert not entry.pending and props.info_pending == 0 and not live.needs_apply(monkey)


def kinds(res):
    return {k: i.kind for k, i in res.edges.items() if len(i.uses) == 2}


def click(action, key):
    """One Edit Cuts click, as the tool does it."""
    e = live.cached(monkey)
    assert editor.apply_pick(monkey, editor.Pick(action, key), e)
    return live.regenerate(scene, ctx.evaluated_depsgraph_get(), monkey, force=True,
                           hold=not props.live, stable=True)


before = kinds(res0)
folds0 = {k for k, v in before.items() if v == KIND_FOLD}
auto0 = [k for k, i in res0.edges.items()
         if i.kind == KIND_CUT and len(i.uses) == 2 and not i.forced_cut]
# A fold in the middle of the biggest piece.
big = res0.islands[0]
real = editor._source_edges(entry)  # not the diagonals of split quads
mine = [k for k in big.fold_keys if k in real]
cut_key = mine[len(mine) // 2]

# ------------------------------------------------ a click only marks the edge
assert editor.edge_action(entry, cut_key) == "cut"
entry = click("cut", cut_key)
assert entry.res is res0 and kinds(entry.res) == before, "the pieces must not move"
assert entry.pending == {cut_key: "cut"} and cut_key in entry.revertable
assert props.info_pending == 1 and live.needs_apply(monkey)
assert editor.edge_action(entry, cut_key) == "revert"
segs = editor.pending_segments(entry)
assert len(segs["cut"]) == 3, "shown on the model and on both sides of the fold on the layout"

# Clicking it again takes the mark back.
entry = click("revert", cut_key)
assert entry.res is res0 and not entry.pending and props.info_pending == 0
assert not any(e.use_seam for e in monkey.data.edges)
print("click: marks the edge, pieces stay put; second click takes it back")

# ------------------------------------------------ tabs and pages still update
entry = click("cut", cut_key)
tab = next(t for t in entry.lay.tabs if t.partner is not None)
face = entry.res.faces[tab.face]
tab_key = editor.edge_key(face.loop[tab.corner], face.loop[(tab.corner + 1) % len(face.loop)])
lay_before = entry.lay
entry = click("flip", tab_key)
assert entry.res is res0 and entry.lay is not lay_before
assert entry.pending == {cut_key: "cut"}, "a tab click leaves the waiting cut waiting"
props.margin = 15.0
live.schedule(monkey)
live._run()
entry = live.cached(monkey)
assert entry.res is res0 and props.info_pending == 1, "page settings re-pack, cuts still wait"
print("tabs and page settings update the layout without applying the cuts")

# ------------------------------------------------ undo empties the cache
members = {k: v for k, v in vars(editor.PAPERCRAFT_OT_edit_cuts).items()
           if callable(v) or isinstance(v, property)}
Fake = type("Fake", (), members)
op = Fake()
op.src_name = monkey.name
op.hover = None
op._take(entry)
live.on_data_reloaded()
assert live.cached(monkey) is None
fake_ctx = SimpleNamespace(scene=scene, evaluated_depsgraph_get=ctx.evaluated_depsgraph_get)
op._refresh_entry(fake_ctx)
assert op.entry.res is res0 and op.entry.pending == {cut_key: "cut"}
print("undo inside Edit Cuts: waiting cuts are still waiting")

# ------------------------------------------------ Apply Cuts
assert bpy.ops.papercraft.apply_cuts() == {"FINISHED"}
entry = live.cached(monkey)
after = kinds(entry.res)
assert entry.res is not res0 and not entry.pending and props.info_pending == 0
assert after[cut_key] == KIND_CUT and entry.res.edges[cut_key].forced_cut
folds1 = {k for k, v in after.items() if v == KIND_FOLD}
# Other folds are kept (bar the odd one where pieces that just touch are
# judged differently when joined in another order).
lost = folds0 - {cut_key} - folds1
assert len(lost) <= 3, len(lost)
changed = sum(1 for k in before if before[k] != after[k])
print(f"Apply Cuts: {len(res0.islands)} -> {len(entry.res.islands)} pieces, "
      f"{changed} edge(s) changed; {live.auto_cuts(entry)} cut(s) are the add-on's own")

# ------------------------------------------------ joins wait too, and win
res1 = entry.res
join_key = next(k for k in auto0 if after[k] == KIND_CUT)
assert editor.edge_action(entry, join_key) == "join"
entry = click("join", join_key)
assert entry.res is res1 and entry.pending == {join_key: "join"}
assert bpy.ops.papercraft.apply_cuts() == {"FINISHED"}
entry = live.cached(monkey)
info = entry.res.edges[join_key]
assert info.kind == KIND_FOLD or any("had to stay cut" in w for w in entry.res.warnings)
kept = len({k for k, v in kinds(entry.res).items() if v == KIND_FOLD} & folds1) / len(folds1)
assert kept > 0.9, kept
assert props.info_pending == 0
print(f"join applied ({info.kind}); {kept:.0%} of the other folds kept")

# ------------------------------------------------ cut settings wait as well
res2 = entry.res
props.max_faces = 12
live.schedule(monkey)
live._run()
entry = live.cached(monkey)
assert entry.res is res2 and props.info_pending == 1 and live.needs_apply(monkey)
assert bpy.ops.papercraft.apply_cuts() == {"FINISHED"}
entry = live.cached(monkey)
assert max(len(i.faces) for i in entry.res.islands) <= 12 and props.info_pending == 0
props.max_faces = 0
assert bpy.ops.papercraft.unfold() == {"FINISHED"}
print("Max Faces per Piece waits for Apply Cuts with Live Layout off")

# ------------------------------------------------ Live Layout on: every click unfolds
props.live = True
entry = live.cached(monkey)
res3 = entry.res
key = next(k for k, v in kinds(res3).items() if v == KIND_FOLD and k in real)
entry = click("cut", key)
assert entry.res is not res3 and entry.res.edges[key].kind == KIND_CUT
assert not entry.pending and props.info_pending == 0
print("Live Layout on: a click cuts at once, as before")

# ------------------------------------------------ core: keep
cube_v = [(-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
          (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)]
cube_f = [(3, 2, 1, 0), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
a = unfold(cube_v, cube_f, priority="long")
fa = {k for k, i in a.edges.items() if i.kind == KIND_FOLD}
b = unfold(cube_v, cube_f, priority="flat", keep=fa)
assert {k for k, i in b.edges.items() if i.kind == KIND_FOLD} == fa
print("core: keep= reproduces an earlier unfold")

addon.unregister()
print("V16 OK")
