# SPDX-License-Identifier: GPL-3.0-or-later
"""Unfold pipeline + debounced live updates."""

import traceback

import bpy
from bpy.app.handlers import persistent

from . import build
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


CACHE = {}  # scene name -> CacheEntry
_state = {"scheduled": False, "force": False}


def regenerate(scene, depsgraph, force=False):
    props = scene.papercraft
    obj = props.source
    if obj is None or obj.name not in scene.objects:
        props.info_message = "Pick a mesh to unfold"
        return None
    src = read_object(obj, depsgraph, props.cut_rules())
    settings = props.settings_key()
    cached = CACHE.get(scene.name)
    if (
        not force
        and cached is not None
        and cached.signature == src.signature
        and cached.settings == settings
    ):
        return cached

    res = unfold(
        src.verts, src.polys, src.seams, src.materials,
        priority=props.priority, max_faces=props.max_faces, joins=src.joins,
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
    )
    _, poly_map, frame = build.build_layout(scene, src, res, lay)

    props.info_islands = len(res.islands)
    props.info_pages = lay.page_count
    props.info_scale = lay.scale
    sizes = [max(v[i] for v in src.verts) - min(v[i] for v in src.verts) for i in range(3)] if src.verts else [0]
    props.info_size_mm = max(sizes) * lay.scale
    props.info_glue = res.glue_count
    props.info_fits = lay.fits
    props.info_message = "; ".join(res.warnings + lay.messages)
    if src.rule_cuts:
        props.info_message = "; ".join(
            [f"{src.rule_cuts} cut(s) from colour/material/sharp rules"]
            + ([props.info_message] if props.info_message else [])
        )
    entry = CacheEntry(src.signature, settings, src, res, lay, poly_map, frame)
    CACHE[scene.name] = entry
    return entry


def get_or_build(context, force=False):
    return regenerate(context.scene, context.evaluated_depsgraph_get(), force=force)


def schedule(force=False):
    _state["force"] = _state["force"] or force
    if _state["scheduled"]:
        return
    _state["scheduled"] = True
    bpy.app.timers.register(_run, first_interval=DEBOUNCE_S)


def _run():
    _state["scheduled"] = False
    force = _state["force"]
    _state["force"] = False
    try:
        context = bpy.context
        regenerate(context.scene, context.evaluated_depsgraph_get(), force=force)
    except Exception as exc:  # keep the timer from dying silently
        traceback.print_exc()
        try:
            bpy.context.scene.papercraft.info_message = f"Error: {exc}"
        except Exception:
            pass
    return None


@persistent
def on_depsgraph_update(scene, depsgraph):
    props = getattr(scene, "papercraft", None)
    if props is None or not props.live or props.source is None:
        return
    src = props.source
    for update in depsgraph.updates:
        idb = update.id
        original = getattr(idb, "original", idb)
        if original == src or (src.data is not None and original == src.data):
            if update.is_updated_geometry or update.is_updated_transform:
                schedule()
                return


def register():
    if on_depsgraph_update not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(on_depsgraph_update)


def unregister():
    if on_depsgraph_update in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(on_depsgraph_update)
    if bpy.app.timers.is_registered(_run):
        bpy.app.timers.unregister(_run)
    _state["scheduled"] = False
    CACHE.clear()
