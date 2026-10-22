# SPDX-License-Identifier: GPL-3.0-or-later
"""Unfold pipeline + debounced live updates, one cache entry per model."""

import traceback

import bpy
from bpy.app.handlers import persistent

from . import build
from . import targets as T
from .core.layout import compute_layout
from .core.unfold import unfold
from .mesh_io import read_object

DEBOUNCE_S = 0.35


class CacheEntry:
    __slots__ = ("signature", "settings", "src", "res", "lay", "poly_map", "frame")

    def __init__(self, signature, settings, src, res, lay, poly_map=None, frame=None):
        self.signature = signature
        self.settings = settings
        self.src = src
        self.res = res
        self.lay = lay
        self.poly_map = poly_map or []
        self.frame = frame


CACHE = {}  # model session_uid -> CacheEntry
_state = {"pending": set(), "force": set(), "scheduled": False}


def _uid(obj):
    return getattr(obj, "session_uid", None) or obj.name_full


def cached(obj):
    return CACHE.get(_uid(obj)) if obj is not None else None


def regenerate(scene, depsgraph, obj, force=False):
    """Unfold `obj` with its own settings and rebuild its layout."""
    if obj is None or obj.name not in scene.objects or obj.type != "MESH":
        return None
    props = obj.papercraft
    if props.role != T.ROLE_SOURCE:
        props.role = T.ROLE_SOURCE
    src = read_object(obj, depsgraph, props.cut_rules())
    settings = props.settings_key()
    entry = CACHE.get(_uid(obj))
    if (
        not force
        and entry is not None
        and entry.signature == src.signature
        and entry.settings == settings
        and T.output(obj, T.ROLE_LAYOUT) is not None
    ):
        # Datablock references go stale after undo/redo; always take the
        # freshly read ones.
        entry.src.mat_list = src.mat_list
        entry.src.images = src.images
        entry.src.colors = src.colors
        return entry

    priority = props.priority
    if priority == "auto" and not force and entry is not None and entry.settings == settings:
        # Live edits: keep the order Auto chose last time (half the work).
        priority = getattr(entry.res, "priority", None) or "auto"
    res = unfold(
        src.verts, src.polys, src.seams, src.materials,
        priority=priority, max_faces=props.max_faces, joins=src.joins,
        valley_bias=props.valley_bias,
    )
    page_w, page_h = props.page_size()
    value = {"FIT": 1.0, "HEIGHT": props.model_size_mm, "RATIO": props.ratio}[props.scale_mode]
    lay = compute_layout(
        res,
        page_w=page_w,
        page_h=page_h,
        margin=props.margin,
        spacing=props.spacing,
        scale_mode=props.scale_mode,
        scale_value=value,
        max_pages=props.pages,
        tab_height=props.tab_height,
        tab_angle=props.tab_angle,
        tab_flips=frozenset((a, b) if a < b else (b, a) for a, b in src.flips),
        tab_outer=frozenset((a, b) if a < b else (b, a) for a, b in src.outer),
    )
    _, poly_map, frame = build.build_layout(scene, obj, src, res, lay)

    props.info_islands = len(res.islands)
    props.info_pages = lay.page_count
    props.info_scale = lay.scale
    lo, hi, _ = build.model_bounds(src.verts)
    props.info_size_mm = max(hi[i] - lo[i] for i in range(3)) * lay.scale
    props.info_glue = res.glue_count
    props.info_fits = lay.fits
    messages = []
    if src.rule_cuts:
        messages.append(f"{src.rule_cuts} cut(s) from colour/material/sharp rules")
    props.info_message = "; ".join(messages + res.warnings + lay.messages)
    entry = CacheEntry(src.signature, settings, src, res, lay, poly_map, frame)
    CACHE[_uid(obj)] = entry
    return entry


def get_or_build(context, obj=None, force=False):
    obj = obj if obj is not None else T.target(context)
    return regenerate(context.scene, context.evaluated_depsgraph_get(), obj, force=force)


def schedule(obj, force=False):
    _state["pending"].add(obj.name)
    if force:
        _state["force"].add(obj.name)
    if _state["scheduled"]:
        return
    _state["scheduled"] = True
    bpy.app.timers.register(_run, first_interval=DEBOUNCE_S)


def _run():
    _state["scheduled"] = False
    pending, forced = _state["pending"], _state["force"]
    _state["pending"], _state["force"] = set(), set()
    context = bpy.context
    for name in pending:
        obj = bpy.data.objects.get(name)
        if obj is None:
            continue
        try:
            regenerate(context.scene, context.evaluated_depsgraph_get(), obj, force=name in forced)
        except Exception as exc:  # keep the timer from dying silently
            traceback.print_exc()
            try:
                obj.papercraft.info_message = f"Error: {exc}"
            except Exception:
                pass
    return None


@persistent
def on_depsgraph_update(scene, depsgraph):
    live_sources = [o for o in T.sources(scene) if o.papercraft.live]
    if not live_sources:
        return
    by_obj = {o: o for o in live_sources}
    by_mesh = {}
    for o in live_sources:
        if o.data is not None:
            by_mesh.setdefault(o.data, []).append(o)
    for update in depsgraph.updates:
        if not (update.is_updated_geometry or update.is_updated_transform):
            continue
        original = getattr(update.id, "original", update.id)
        if original in by_obj:
            schedule(original)
        elif original in by_mesh:
            for o in by_mesh[original]:
                schedule(o)


@persistent
def on_data_reloaded(*_args):
    """Undo, redo and file loads replace every datablock: drop cached
    references so nothing touches a removed Material/Object."""
    CACHE.clear()


_RELOAD_HANDLERS = ("undo_post", "redo_post", "load_post")


def register():
    if on_depsgraph_update not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(on_depsgraph_update)
    for name in _RELOAD_HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if on_data_reloaded not in handlers:
            handlers.append(on_data_reloaded)


def unregister():
    if on_depsgraph_update in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(on_depsgraph_update)
    for name in _RELOAD_HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if on_data_reloaded in handlers:
            handlers.remove(on_data_reloaded)
    if bpy.app.timers.is_registered(_run):
        bpy.app.timers.unregister(_run)
    _state["scheduled"] = False
    _state["pending"].clear()
    CACHE.clear()
