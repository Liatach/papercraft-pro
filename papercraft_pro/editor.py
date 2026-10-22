# SPDX-License-Identifier: GPL-3.0-or-later
"""Click-to-cut editing, Pepakura style.

Hover an edge on the 3D model or on the paper layout: it lights up in both
places. Click to cut a fold or join a cut; click a tab to move it to the
other side of its seam. Works across split viewports.
"""

import bpy
import gpu
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader
from mathutils import Vector
from mathutils.geometry import intersect_point_line

from . import build, live
from . import targets as T
from .core.unfold import KIND_FOLD, edge_key
from .mesh_io import FLIP_ATTR, JOIN_ATTR, OUTER_ATTR

COLORS = {
    "cut": (1.0, 0.35, 0.1, 1.0),  # hover: will cut
    "join": (0.2, 0.85, 0.3, 1.0),  # hover: will join
    "flip": (0.25, 0.6, 1.0, 1.0),  # hover: tab will move to the other side
    "outer": (0.75, 0.3, 1.0, 1.0),  # hover (Shift): tab inside <-> outside
    "seam": (0.9, 0.1, 0.1, 0.9),  # existing cuts on the model
}


class Pick:
    __slots__ = ("action", "key", "tab", "segments")

    def __init__(self, action, key, tab=None, segments=()):
        self.action = action
        self.key = key
        self.tab = tab
        self.segments = list(segments)


# ----------------------------------------------------------------- picking
def _nearest_edge(point, corners):
    """Index k of the polygon edge corners[k] -> corners[k+1] nearest point."""
    best_k, best_d = 0, float("inf")
    count = len(corners)
    for k in range(count):
        a, b = corners[k], corners[(k + 1) % count]
        closest, t = intersect_point_line(point, a, b)
        if t < 0.0:
            closest = a
        elif t > 1.0:
            closest = b
        d = (point - closest).length
        if d < best_d:
            best_k, best_d = k, d
    return best_k


def _ray_hit(obj, origin, direction):
    if obj is None or not obj.visible_get():
        return None
    inv = obj.matrix_world.inverted()
    o = inv @ origin
    d = (inv.to_3x3() @ direction).normalized()
    ok, loc, _normal, index = obj.ray_cast(o, d)
    if not ok:
        return None
    world = obj.matrix_world @ loc
    return (world - origin).length, world, index


_EDGE_CACHE = {"sig": None, "keys": set()}


def _source_edges(entry):
    if _EDGE_CACHE["sig"] != entry.src.signature:
        keys = set()
        for loop in entry.src.polys:
            for k in range(len(loop)):
                keys.add(edge_key(loop[k], loop[(k + 1) % len(loop)]))
        _EDGE_CACHE["sig"] = entry.src.signature
        _EDGE_CACHE["keys"] = keys
    return _EDGE_CACHE["keys"]


def layout_segments(entry, key, lift=0.2):
    """Every place an edge appears on the paper layout (world space)."""
    res, lay, frame = entry.res, entry.lay, entry.frame
    info = res.edges.get(key)
    if info is None or frame is None:
        return []
    island_of = {}
    for isl in res.islands:
        for f in isl.faces:
            island_of[f] = isl
    out = []
    for f, k in info.uses:
        isl = island_of.get(f)
        if isl is None:
            continue
        page = lay.placements[isl.index].page
        pts = lay.face_points(res, isl, f)
        a, b = pts[k], pts[(k + 1) % len(pts)]
        out.append((frame.to_world(page, a, lift), frame.to_world(page, b, lift)))
    return out


def edge_action(entry, key):
    info = entry.res.edges.get(key)
    if info is None or len(info.uses) < 2:
        return None  # open boundary: nothing to join
    if key in entry.src.user_seams:
        return "join"
    return "cut" if info.kind == KIND_FOLD else "join"


def pick(src_obj, entry, origin, direction):
    """What is under this ray? Returns a Pick or None."""
    hits = []
    h = _ray_hit(src_obj, origin, direction)
    if h is not None:
        hits.append(("model",) + h)
    lay_obj = T.output(src_obj, T.ROLE_LAYOUT)
    h = _ray_hit(lay_obj, origin, direction)
    if h is not None:
        hits.append(("layout",) + h)
    if not hits:
        return None
    where, _dist, point, index = min(hits, key=lambda x: x[1])
    verts = entry.src.verts

    if where == "model":
        if index >= len(entry.src.polys):
            return None
        loop = entry.src.polys[index]
        corners = [Vector(verts[v]) for v in loop]
        k = _nearest_edge(point, corners)
        key = edge_key(loop[k], loop[(k + 1) % len(loop)])
    else:
        if index >= len(entry.poly_map):
            return None
        kind, ref = entry.poly_map[index]
        if kind == "tab":
            tab = entry.lay.tabs[ref]
            face = entry.res.faces[tab.face]
            key = edge_key(face.loop[tab.corner], face.loop[(tab.corner + 1) % len(face.loop)])
            page = entry.lay.placements[tab.island].page
            pts = [entry.frame.to_world(page, p, 0.2) for p in entry.lay.tab_points(tab)]
            segs = [(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts))]
            return Pick("flip", key, ref, segs)
        if kind != "face":
            return None
        face = entry.res.faces[ref]
        poly = lay_obj.data.polygons[index]
        corners = [lay_obj.matrix_world @ lay_obj.data.vertices[v].co for v in poly.vertices]
        k = _nearest_edge(point, corners)
        key = edge_key(face.loop[k], face.loop[(k + 1) % len(face.loop)])
        if key not in _source_edges(entry):
            return None  # diagonal of a split face, not a real edge

    action = edge_action(entry, key)
    if action is None:
        return None
    a, b = key
    segs = [(Vector(verts[a]), Vector(verts[b]))] + layout_segments(entry, key)
    return Pick(action, key, None, segs)


# ----------------------------------------------------------------- applying
def _edge_index(mesh, key):
    for e in mesh.edges:
        v = e.vertices
        if (v[0], v[1]) == key or (v[1], v[0]) == key:
            return e.index
    return None


def _int_attr(mesh, name):
    attr = mesh.attributes.get(name)
    if attr is None:
        attr = mesh.attributes.new(name, "INT", "EDGE")
    return attr


def apply_pick(src_obj, p):
    """Write the user's choice onto the source mesh. Returns True if changed."""
    mesh = src_obj.data
    ei = _edge_index(mesh, p.key)
    if ei is None:
        return False
    if p.action == "flip":
        attr = _int_attr(mesh, FLIP_ATTR)
        attr.data[ei].value = 0 if attr.data[ei].value else 1
    elif p.action == "outer":
        attr = _int_attr(mesh, OUTER_ATTR)
        attr.data[ei].value = 0 if attr.data[ei].value else 1
    elif p.action == "cut":
        mesh.edges[ei].use_seam = True
        attr = mesh.attributes.get(JOIN_ATTR)
        if attr is not None:
            attr.data[ei].value = 0
    elif p.action == "join":
        mesh.edges[ei].use_seam = False
        _int_attr(mesh, JOIN_ATTR).data[ei].value = 1
    mesh.update()
    return True


def model_cut_segments(entry):
    verts = entry.src.verts
    out = []
    for key, info in entry.res.edges.items():
        if info.kind != KIND_FOLD and len(info.uses) == 2:
            out.append((Vector(verts[key[0]]), Vector(verts[key[1]])))
    return out


# ----------------------------------------------------------------- drawing
# The running tool, so the panel button can stop it and a file load can
# never leave its highlight lines behind.
_ACTIVE = {"op": None, "handle": None, "workspace": None, "stop": False}


def is_running():
    return _ACTIVE["op"] is not None


def _remove_overlay():
    handle, _ACTIVE["handle"] = _ACTIVE["handle"], None
    if handle is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(handle, "WINDOW")
        except (ValueError, RuntimeError):
            pass
    ws, _ACTIVE["workspace"] = _ACTIVE["workspace"], None
    for w in {ws, getattr(bpy.context, "workspace", None)}:
        if w is not None:
            try:
                w.status_text_set(None)
            except (ReferenceError, AttributeError, RuntimeError):
                pass
    _ACTIVE["op"] = None
    _ACTIVE["stop"] = False


def _draw(op):
    try:
        cut_segments, hover = op.cut_segments, op.hover
    except Exception:  # the tool ended without cleaning up: drop the overlay
        _remove_overlay()
        return
    try:
        shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
    except (ValueError, SystemError):
        return
    region = bpy.context.region
    gpu.state.blend_set("ALPHA")
    gpu.state.depth_test_set("LESS_EQUAL")
    shader.bind()
    shader.uniform_float("viewportSize", (region.width, region.height))

    def lines(segs, color, width):
        if not segs:
            return
        coords = [c for seg in segs for c in seg]
        batch = batch_for_shader(shader, "LINES", {"pos": coords})
        shader.uniform_float("lineWidth", width)
        shader.uniform_float("color", color)
        batch.draw(shader)

    try:
        lines(cut_segments, COLORS["seam"], 2.0)
        gpu.state.depth_test_set("NONE")
        if hover is not None:
            lines(hover.segments, COLORS[hover.action], 5.0)
    finally:
        gpu.state.depth_test_set("NONE")
        gpu.state.blend_set("NONE")


def _region_under_mouse(context, event):
    """(area, region) under the mouse in this window, preferring the
    overlapping sidebar/toolbar/header regions over the 3D view itself."""
    for area in context.window.screen.areas:
        if not (area.x <= event.mouse_x < area.x + area.width
                and area.y <= event.mouse_y < area.y + area.height):
            continue
        hit = None
        for region in area.regions:
            if (region.width > 1 and region.height > 1
                    and region.x <= event.mouse_x < region.x + region.width
                    and region.y <= event.mouse_y < region.y + region.height):
                if region.type != "WINDOW":
                    return area, region
                hit = region
        return area, hit
    return None, None


def _view_under_mouse(context, event):
    """(region, rv3d, local coords) of the 3D view under the mouse, or None
    when the mouse is over a panel, header or another editor."""
    area, region = _region_under_mouse(context, event)
    if area is None or region is None or area.type != "VIEW_3D" or region.type != "WINDOW":
        return None
    return region, area.spaces.active.region_3d, (event.mouse_x - region.x, event.mouse_y - region.y)


def _redraw(context):
    for area in context.window.screen.areas:
        if area.type == "VIEW_3D":
            area.tag_redraw()


PASS_THROUGH_EVENTS = {
    "MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE", "TRACKPADPAN", "TRACKPADZOOM",
    "NDOF_MOTION", "MOUSEROTATE", "MOUSESMARTZOOM",
}


class PAPERCRAFT_OT_edit_cuts(bpy.types.Operator):
    bl_idname = "papercraft.edit_cuts"
    bl_label = "Edit Cuts"
    bl_description = (
        "Click edges on the model or the paper layout to cut or join them; "
        "click a tab to move it to the other side; Shift+click a tab to glue it "
        "on the outside (printed with the art it covers). Press again, "
        "right-click or Esc to finish"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if is_running():
            return True  # the button doubles as "Done"
        return (
            T.target(context) is not None
            and context.area is not None
            and context.area.type == "VIEW_3D"
        )

    def invoke(self, context, event):
        if is_running():  # pressed again: tell the running tool to finish
            _ACTIVE["stop"] = True
            _redraw(context)
            return {"CANCELLED"}
        _remove_overlay()  # stale leftovers from a crashed session, if any
        src_obj = T.target(context)
        build.ensure_object_mode(context)  # cuts are written in Object Mode
        context.view_layer.objects.active = src_obj
        src_obj.papercraft.role = T.ROLE_SOURCE
        entry = live.get_or_build(context, src_obj)
        if entry is None:
            return {"CANCELLED"}
        if not entry.src.topology_ok:
            self.report(
                {"ERROR"},
                "Edit Cuts needs the model without topology-changing modifiers "
                "(apply or disable them first)",
            )
            return {"CANCELLED"}
        self.src_name = src_obj.name
        self.entry = entry
        self.hover = None
        self.cut_segments = model_cut_segments(entry)
        self.changes = 0
        _ACTIVE["op"] = self
        _ACTIVE["stop"] = False
        _ACTIVE["handle"] = bpy.types.SpaceView3D.draw_handler_add(
            _draw, (self,), "WINDOW", "POST_VIEW"
        )
        _ACTIVE["workspace"] = context.workspace
        context.window_manager.modal_handler_add(self)
        context.workspace.status_text_set(
            "Edit Cuts — click edge: cut / join  ·  click tab: other side of the seam  ·  "
            "Shift+click tab: inside / outside  ·  Right-click / Esc / Done button: finish"
        )
        _redraw(context)
        return {"RUNNING_MODAL"}

    @property
    def src_obj(self):
        return bpy.data.objects.get(self.src_name)

    def _finish(self, context):
        _remove_overlay()
        src_obj = self.src_obj
        # Keep an existing fold rig in step with the new cuts (keyframes kept).
        if self.changes and src_obj is not None and T.output(src_obj, T.ROLE_RIG) is not None:
            try:
                entry = live.cached(src_obj) or live.get_or_build(context, src_obj)
                build.build_rig(context, src_obj, entry)
                self.report({"INFO"}, "Fold rig rebuilt to match the new cuts")
            except Exception as exc:  # never leave the user stuck in the tool
                self.report({"WARNING"}, f"Rig not rebuilt: {exc}")
        _redraw(context)
        return {"FINISHED"} if self.changes else {"CANCELLED"}

    def _refresh_entry(self, context):
        """Undo/redo drop the cache: rebuild so picks match the mesh again."""
        src_obj = self.src_obj
        if live.cached(src_obj) is self.entry:
            return
        entry = live.cached(src_obj) or live.get_or_build(context, src_obj)
        if entry is not None:
            self.entry = entry
            self.cut_segments = model_cut_segments(entry)
            self.hover = None

    def _update_hover(self, context, event):
        view = _view_under_mouse(context, event)
        self.hover = None
        if view is None:
            return
        region, rv3d, coord = view
        origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
        direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
        src_obj = self.src_obj
        self.hover = pick(src_obj, self.entry, origin, direction) if src_obj else None
        if self.hover is not None and self.hover.action == "flip" and event.shift:
            self.hover.action = "outer"  # Shift: glue inside <-> outside

    def modal(self, context, event):
        try:
            return self._modal(context, event)
        except Exception as exc:  # an error must end the tool, not strand it
            import traceback
            traceback.print_exc()
            self.report({"ERROR"}, f"Edit Cuts stopped: {exc}")
            self._finish(context)
            return {"CANCELLED"}

    def _modal(self, context, event):
        src_obj = self.src_obj
        if _ACTIVE["stop"] or _ACTIVE["op"] is not self:
            return self._finish(context)
        if src_obj is None or src_obj.mode != "OBJECT":
            # Model deleted, or the user went into Edit/Paint mode: finish
            # cleanly (cuts are clicked in Object Mode).
            return self._finish(context)
        if event.type in {"RIGHTMOUSE", "ESC"} and event.value == "PRESS":
            if _view_under_mouse(context, event) is None and event.type == "RIGHTMOUSE":
                return {"PASS_THROUGH"}  # right-click menus on panels still work
            return self._finish(context)
        if event.type in PASS_THROUGH_EVENTS:
            return {"PASS_THROUGH"}
        self._refresh_entry(context)
        if event.type in {"MOUSEMOVE", "LEFT_SHIFT", "RIGHT_SHIFT"}:
            self._update_hover(context, event)
            _redraw(context)
            return {"PASS_THROUGH"}
        if event.type == "LEFTMOUSE":
            if _view_under_mouse(context, event) is None:
                return {"PASS_THROUGH"}  # panels, headers, other editors
            if event.value != "PRESS":
                return {"RUNNING_MODAL"}
            self._update_hover(context, event)
            if self.hover is not None and apply_pick(src_obj, self.hover):
                entry = live.regenerate(
                    context.scene, context.evaluated_depsgraph_get(), src_obj, force=True
                )
                if entry is not None:
                    self.entry = entry
                    self.cut_segments = model_cut_segments(entry)
                self.changes += 1
                bpy.ops.ed.undo_push(message="Papercraft: edit cut")
                self._update_hover(context, event)
                _redraw(context)
            return {"RUNNING_MODAL"}
        return {"PASS_THROUGH"}

    def cancel(self, context):  # Blender cancelled us (window closed, file load)
        _remove_overlay()


@bpy.app.handlers.persistent
def _on_load_pre(*_args):
    _remove_overlay()


classes = (PAPERCRAFT_OT_edit_cuts,)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    if _on_load_pre not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(_on_load_pre)


def unregister():
    _remove_overlay()
    if _on_load_pre in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_on_load_pre)
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
