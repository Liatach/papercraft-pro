"""v0.7: Edit Cuts can always be left (button, errors, undo, mode change, file load)."""
import os
import sys
from types import SimpleNamespace

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import papercraft_pro as addon  # noqa: E402
from papercraft_pro import editor, live  # noqa: E402

bpy.ops.wm.read_factory_settings(use_empty=True)
addon.register()
ctx = bpy.context
bpy.ops.mesh.primitive_cube_add()
cube = ctx.active_object
assert bpy.ops.papercraft.unfold() == {"FINISHED"}

# Operators can't be instantiated outside a running UI: drive the same
# methods on a stand-in object.
OP = editor.PAPERCRAFT_OT_edit_cuts
members = {k: v for k, v in vars(OP).items() if callable(v) or isinstance(v, property)}
Fake = type("Fake", (), members)
reports = []
Fake.report = lambda self, kind, msg: reports.append((kind, msg))


class Ctx:  # context without a window: nothing is under the mouse
    scene = ctx.scene
    workspace = None
    window = SimpleNamespace(screen=SimpleNamespace(areas=[]))

    def evaluated_depsgraph_get(self):
        return ctx.evaluated_depsgraph_get()


C = Ctx()


def start():
    op = Fake()
    op.src_name = cube.name
    op.entry = live.cached(cube) or live.get_or_build(ctx, cube)
    op.hover = None
    op.cut_segments = editor.model_cut_segments(op.entry)
    op.changes = 0
    editor._ACTIVE.update(op=op, stop=False, workspace=None,
                          handle=bpy.types.SpaceView3D.draw_handler_add(editor._draw, (op,), "WINDOW", "POST_VIEW"))
    return op


def ev(type_, value="NOTHING", shift=False):
    return SimpleNamespace(type=type_, value=value, shift=shift, mouse_x=10, mouse_y=10)


# 1. Pressing the panel button again finishes the tool.
op = start()
assert editor.is_running()
assert OP.poll(ctx)  # button stays clickable while running
editor._ACTIVE["stop"] = True
assert op.modal(C, ev("MOUSEMOVE")) == {"CANCELLED"}
assert not editor.is_running() and editor._ACTIVE["handle"] is None
print("button again: finished, overlay removed")

# 2. Clicks outside a 3D view pass through to the UI instead of being eaten.
op = start()
assert op.modal(C, ev("LEFTMOUSE", "PRESS")) == {"PASS_THROUGH"}
assert op.modal(C, ev("RIGHTMOUSE", "PRESS")) == {"PASS_THROUGH"}
assert op.modal(C, ev("ESC", "PRESS")) == {"CANCELLED"} and not editor.is_running()
print("UI clicks pass through; Esc anywhere finishes")

# 3. An error mid-edit ends the tool cleanly.
op = start()
op._update_hover = lambda *a: (_ for _ in ()).throw(RuntimeError("boom"))
assert op.modal(C, ev("MOUSEMOVE")) == {"CANCELLED"}
assert not editor.is_running() and editor._ACTIVE["handle"] is None
assert any("boom" in m for _, m in reports)
print("error: tool ended, overlay removed")

# 4. Undo (cache dropped) rebuilds the entry instead of using stale data.
op = start()
old = op.entry
live.on_data_reloaded()
op.modal(C, ev("MOUSEMOVE"))
assert op.entry is not old and live.cached(cube) is op.entry
print("undo: entry refreshed")

# 5. Entering Edit Mode ends the tool.
ctx.view_layer.objects.active = cube
bpy.ops.object.mode_set(mode="EDIT")
assert op.modal(C, ev("MOUSEMOVE")) == {"CANCELLED"} and not editor.is_running()
bpy.ops.object.mode_set(mode="OBJECT")
print("edit mode: tool ended")

# 6. A stale overlay whose operator is gone removes itself; file load clears it.
op = start()
editor._ACTIVE["op"] = None
editor._draw(SimpleNamespace())  # no attributes: like a freed operator
assert editor._ACTIVE["handle"] is None
op = start()
editor._on_load_pre()
assert not editor.is_running() and editor._ACTIVE["handle"] is None
print("stale overlay and file load: cleaned up")

addon.unregister()
print("V07 OK")
