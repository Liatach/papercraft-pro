"""v0.9: paper thickness. Glue tabs end up one paper thickness behind the
wall they glue to (outside tabs: in front), never in its plane; the
Solidify is added for you and hand-added modifiers survive rebuilds."""
import os
import sys

import bpy
from mathutils import Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import papercraft_pro as addon  # noqa: E402
from papercraft_pro import build, editor, live  # noqa: E402
from papercraft_pro import targets as T  # noqa: E402

bpy.ops.wm.read_factory_settings(use_empty=True)
addon.register()
ctx = bpy.context
scene = ctx.scene

bpy.ops.mesh.primitive_cube_add()
cube = ctx.active_object
ctx.view_layer.objects.active = cube
props = cube.papercraft
props.texture_mode = "WHITE"
assert bpy.ops.papercraft.unfold() == {"FINISHED"}
entry = live.cached(cube)
# Make one tab an outside tab.
tab0 = entry.lay.tabs[0]
face = entry.res.faces[tab0.face]
key = (face.loop[tab0.corner], face.loop[(tab0.corner + 1) % len(face.loop)])
p = editor.Pick("outer", tuple(sorted(key)))
assert editor.apply_pick(cube, p)
entry = live.regenerate(scene, ctx.evaluated_depsgraph_get(), cube, force=True)
assert sum(t.outer for t in entry.lay.tabs) == 1

props.paper_thickness = 0.3
assert bpy.ops.papercraft.build_rig() == {"FINISHED"}
arm = T.output(cube, T.ROLE_RIG)
folding = T.output(cube, T.ROLE_FOLDING)
solid = folding.modifiers.get("Paper Thickness")
assert solid is not None and solid.offset == -1.0 and not solid.use_rim
unit = entry.frame.unit if entry.frame else 1.0 / entry.lay.scale
assert abs(solid.thickness - 0.3 * unit) < 1e-9
print(f"Solidify added: {solid.thickness:.5f} units = 0.3 mm of paper")


def positions(fold, assemble):
    arm["fold"], arm["assemble"] = fold, assemble
    arm.update_tag()
    scene.frame_set(scene.frame_current)
    # Read the rig deformation only (before the Solidify adds vertices).
    solid.show_viewport = False
    ev = folding.evaluated_get(ctx.evaluated_depsgraph_get())
    me = ev.to_mesh()
    pos = [ev.matrix_world @ v.co for v in me.vertices]
    ev.to_mesh_clear()
    solid.show_viewport = True
    return pos


def check(gap_mm):
    pos = positions(1.0, 1.0)
    idx = sum(len(entry.res.faces[f].loop) for isl in entry.res.islands for f in isl.order)
    worst_in, worst_out = 0.0, 0.0
    for tab in entry.lay.tabs:
        n_pts = len(entry.lay.tab_points(tab))
        partner = entry.res.faces[tab.partner[0]]
        pv = [Vector(entry.src.verts[v]) for v in partner.loop]
        normal = build._newell(pv)
        d = [(pos[i] - pv[0]).dot(normal) / unit for i in range(idx, idx + n_pts)]
        idx += n_pts
        want = gap_mm * 1.1 * (1 if tab.outer else -1)
        err = max(abs(x - want) for x in d)
        if tab.outer:
            worst_out = max(worst_out, err)
        else:
            worst_in = max(worst_in, err)
    return worst_in, worst_out


win, wout = check(0.3)
print(f"folded + assembled: inside tabs 0.33 mm behind their wall (err {win:.1e} mm), "
      f"outside tab 0.33 mm in front (err {wout:.1e} mm)")
assert win < 1e-3 and wout < 1e-3

# Flat sheet stays flat (no slide before folding).
pos0 = positions(0.0, 0.0)
assert max(abs(p.z - pos0[0].z) for p in pos0) < 1e-6

# Hand-added modifiers survive a rebuild; a hand Solidify yields to Paper Thickness.
folding.modifiers.new("My Bevel", "BEVEL").width = 0.0123
folding.modifiers.new("Old Solidify", "SOLIDIFY")
assert build.rig_is_stale(cube, entry) is False
bpy.ops.papercraft.build_rig()
folding = T.output(cube, T.ROLE_FOLDING)
names = [m.name for m in folding.modifiers]
print("modifiers after rebuild:", names)
assert names == ["Fold Rig", "Paper Thickness", "My Bevel"]
assert abs(folding.modifiers["My Bevel"].width - 0.0123) < 1e-9

# Thickness 0: flat paper, tabs in the wall plane as before.
props.paper_thickness = 0.0
assert build.rig_is_stale(cube, entry)
bpy.ops.papercraft.build_rig()
arm = T.output(cube, T.ROLE_RIG)
folding = T.output(cube, T.ROLE_FOLDING)
assert folding.modifiers.get("Paper Thickness") is None
solid = folding.modifiers.new("x", "SOLIDIFY")  # stand-in so positions() works
win, wout = check(0.0)
assert win < 1e-3 and wout < 1e-3
print("thickness 0: tabs lie on their walls as before")

addon.unregister()
print("V09 OK")
