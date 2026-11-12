# SPDX-License-Identifier: GPL-3.0-or-later
"""Unfold pipeline + debounced live updates, one cache entry per model."""

import math
import traceback

import bpy
from bpy.app.handlers import persistent

from . import build
from . import targets as T
from .core.board import apply_bend_allowance, clear_bend_allowance
from .core.layout import compute_layout
from .core.unfold import KIND_CUT, KIND_FOLD, edge_key, unfold
from .mesh_io import read_object

DEBOUNCE_S = 0.35


class CacheEntry:
    __slots__ = ("signature", "settings", "src", "res", "lay", "poly_map", "frame",
                 "cut_signature", "unfold_settings", "pending", "revertable")

    def __init__(self, signature, settings, src, res, lay, poly_map=None, frame=None,
                 cut_signature=None, unfold_settings=None):
        self.signature = signature
        self.settings = settings
        # What `res` was unfolded from. With Live Layout off these lag behind
        # the mesh and the settings until Apply Cuts.
        self.cut_signature = cut_signature
        self.unfold_settings = unfold_settings
        self.pending = {}  # edge key -> "cut" / "join" / "free": waiting for Apply Cuts
        self.revertable = set()  # pending edges the user marked (a click takes them back)
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


def _keys(pairs):
    return frozenset(edge_key(a, b) for a, b in pairs)


def _pending(src, res):
    """Cut and join marks on the mesh that `res` was not unfolded with:
    ({edge key: "cut" / "join" / "free"}, {keys the user marked})."""
    seams, joins = _keys(src.seams), _keys(src.joins)
    was_seams = getattr(res, "applied_seams", seams)
    was_joins = getattr(res, "applied_joins", joins)
    pending = {}
    for key in (seams ^ was_seams) | (joins ^ was_joins):
        if key in seams:
            pending[key] = "cut"
        elif key in joins:
            pending[key] = "join"
        else:
            pending[key] = "free"  # mark removed: Apply Cuts decides
    user = getattr(res, "applied_user_seams", src.user_seams)
    return pending, (set(src.user_seams) ^ set(user)) | (joins ^ was_joins)


def needs_apply(obj):
    """True when cuts, joins or cut settings are waiting for Apply Cuts."""
    props = obj.papercraft
    if props.info_pending:
        return True
    entry = CACHE.get(_uid(obj))
    return entry is not None and entry.unfold_settings != props.unfold_key()


def auto_cuts(entry):
    """Cuts the unfold added itself (not asked for by a seam, rule or setting)."""
    return sum(1 for info in entry.res.edges.values()
               if info.kind == KIND_CUT and len(info.uses) == 2 and not info.forced_cut)


def restore(obj, entry):
    """Put back an entry dropped by undo/redo (Edit Cuts holds one), so cuts
    still waiting are not applied just because the cache was emptied. The
    caller must regenerate at once: the entry's datablock references are
    stale."""
    CACHE[_uid(obj)] = entry


def regenerate(scene, depsgraph, obj, force=False, full=False, hold=False, stable=False):
    """Unfold `obj` with its own settings and rebuild its layout.

    force: rebuild even if nothing seems to have changed.
    full: unfold again even if the cuts and unfold settings are the same
    (otherwise page, scale and tab changes just re-pack the last unfold).
    hold: don't unfold again for new cut/join marks or cut settings: keep the
    pieces as they are and note the changes as pending (Apply Cuts). Ignored
    if the mesh itself changed, or with `full`.
    stable: when unfolding again, fold the edges that were folded last time
    first, so pieces away from the changes keep their shape."""
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

    unfold_settings = props.unfold_key()
    cut_signature = src.cut_signature
    if (
        not full
        and entry is not None
        and entry.cut_signature == src.cut_signature
        and entry.unfold_settings == unfold_settings
    ):
        # Same cuts: only the pages, scale or tabs changed. Re-pack the
        # pieces already unfolded, so they keep their shapes.
        res = entry.res
    elif (hold and not full and entry is not None
          and entry.src.geom_signature == src.geom_signature):
        # Cuts marked but not applied yet: the pieces stay as they are.
        res = entry.res
        cut_signature, unfold_settings = entry.cut_signature, entry.unfold_settings
    else:
        priority = props.priority
        keep = ()
        if entry is not None and stable:
            keep = [k for k, i in entry.res.edges.items() if i.kind == KIND_FOLD]
        if priority == "auto" and entry is not None and (
                stable or (not force and entry.settings == settings)):
            # Live edits: keep the order Auto chose last time (half the work).
            priority = getattr(entry.res, "priority", None) or "auto"
        res = unfold(
            src.verts, src.polys, src.seams, src.materials,
            priority=priority, max_faces=props.max_faces, joins=src.joins,
            valley_bias=props.valley_bias,
            cut_valleys=math.radians(max(props.fold_min_angle, 1.0)) if props.cut_valleys else None,
            keep=keep,
        )
        res.applied_seams = _keys(src.seams)
        res.applied_joins = _keys(src.joins)
        res.applied_user_seams = frozenset(src.user_seams)
    page_w, page_h = props.page_size()
    value = {"FIT": 1.0, "HEIGHT": props.model_size_mm, "RATIO": props.ratio}[props.scale_mode]

    def layout():
        return compute_layout(
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
            tab_flips=_keys(src.flips),
            tab_outer=_keys(src.outer),
            use_tabs=props.join_style == "TABS",
            open_tabs=_keys(src.open_tabs),
            arrange=props.arrange,
        )

    clear_bend_allowance(res)  # a re-used unfold may carry an earlier one
    lay = layout()
    if props.fold_style == "CREASE" and props.fold_radius > 0.0:
        # Creased board: shorten pieces at each fold for the bend. The
        # shortening is in mm, so it depends on the scale; re-run if the
        # layout's scale moves (Fit to Pages).
        for _ in range(3):
            scale = lay.scale
            apply_bend_allowance(res, scale, props.fold_radius, props.board_thickness,
                                 math.radians(props.fold_min_angle))
            lay = layout()
            if abs(lay.scale / scale - 1.0) < 0.002:
                break
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
    if props.fold_style == "VGROOVE" and props.board_thickness >= 1.0:
        inward = sum(1 for i in res.edges.values()
                     if i.kind == "fold" and i.angle < -math.radians(max(props.fold_min_angle, 1.0)))
        if inward:
            messages.append(
                f"{inward} inward fold(s) can't be V-grooved in thick board: cut them "
                "(Cut Valley Folds; your Edit Cuts joins override it) or crease them"
            )
    props.info_message = "; ".join(messages + res.warnings + lay.messages)
    entry = CacheEntry(src.signature, settings, src, res, lay, poly_map, frame,
                       cut_signature, unfold_settings)
    entry.pending, entry.revertable = _pending(src, res)
    waiting = len(entry.pending) + (unfold_settings != props.unfold_key())
    if props.info_pending != waiting:
        props.info_pending = waiting
    CACHE[_uid(obj)] = entry
    return entry


def get_or_build(context, obj=None, force=False, full=False, hold=False, stable=False):
    obj = obj if obj is not None else T.target(context)
    return regenerate(context.scene, context.evaluated_depsgraph_get(), obj, force=force,
                      full=full, hold=hold, stable=stable)


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
            # Live Layout off: settings re-pack the pieces; cuts wait for Apply Cuts.
            regenerate(context.scene, context.evaluated_depsgraph_get(), obj,
                       force=name in forced, hold=not obj.papercraft.live)
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
