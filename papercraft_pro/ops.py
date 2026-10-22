# SPDX-License-Identifier: GPL-3.0-or-later
import os

import bpy
from bpy.props import IntProperty, StringProperty
from bpy_extras.io_utils import ExportHelper

from . import build, live
from . import targets as T
from .core.svg import export_pages
from .textures import textures_for


def _target(context):
    return T.target(context)


class _NeedsModel:
    @classmethod
    def poll(cls, context):
        return _target(context) is not None


class PAPERCRAFT_OT_unfold(_NeedsModel, bpy.types.Operator):
    bl_idname = "papercraft.unfold"
    bl_label = "Unfold"
    bl_description = "Unfold this model and lay its pieces out on pages beside it"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = _target(context)
        obj.papercraft.role = T.ROLE_SOURCE
        entry = live.get_or_build(context, obj, force=True)
        if entry is None:
            self.report({"WARNING"}, obj.papercraft.info_message or "Nothing to unfold")
            return {"CANCELLED"}
        self.report(
            {"INFO"},
            f"{obj.name}: {len(entry.res.islands)} piece(s) on {entry.lay.page_count} "
            f"page(s), model {obj.papercraft.info_size_mm:.0f} mm",
        )
        return {"FINISHED"}


class PAPERCRAFT_OT_clear(_NeedsModel, bpy.types.Operator):
    bl_idname = "papercraft.clear"
    bl_label = "Remove Papercraft"
    bl_description = "Delete this model's layout and fold rig (the model itself is untouched)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = _target(context)
        saved = build.ensure_object_mode(context)
        build.remove_rig(obj)
        build.remove_layout(obj)
        obj.papercraft.role = T.ROLE_NONE
        obj.papercraft.live = False
        obj.papercraft.info_islands = 0
        obj.papercraft.info_message = ""
        live.CACHE.pop(live._uid(obj), None)
        context.view_layer.objects.active = obj
        build.restore_mode(context, (obj.name, saved[1]))
        return {"FINISHED"}


class PAPERCRAFT_OT_frame_layout(_NeedsModel, bpy.types.Operator):
    bl_idname = "papercraft.frame_layout"
    bl_label = "Show Layout"
    bl_description = (
        "Look straight down at this model's paper layout in this viewport "
        "(switches to Material Preview so the art shows)"
    )

    @classmethod
    def poll(cls, context):
        obj = _target(context)
        return (
            obj is not None
            and context.area is not None
            and context.area.type == "VIEW_3D"
            and T.output(obj, T.ROLE_LAYOUT) is not None
        )

    def execute(self, context):
        src = _target(context)
        lay = T.output(src, T.ROLE_LAYOUT)
        saved = build.ensure_object_mode(context)
        for o in context.selected_objects:
            o.select_set(False)
        lay.hide_set(False)
        lay.select_set(True)
        context.view_layer.objects.active = lay
        bpy.ops.view3d.view_axis(type="TOP")
        bpy.ops.view3d.view_selected()
        shading = context.space_data.shading
        if shading.type in ("WIREFRAME", "SOLID"):
            # Solid mode can't show image/painted textures: the pieces would
            # look like blank grey shapes.
            shading.type = "MATERIAL"
        lay.select_set(False)
        src.select_set(True)
        context.view_layer.objects.active = src
        build.restore_mode(context, (src.name, saved[1]))
        return {"FINISHED"}


class PAPERCRAFT_OT_reset_layout(_NeedsModel, bpy.types.Operator):
    bl_idname = "papercraft.reset_layout"
    bl_label = "Reset Layout Position"
    bl_description = "Move the paper layout back beside the model"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = _target(context)
        entry = live.get_or_build(context, obj)
        if entry is not None:
            build.reset_layout_position(obj, entry)
        return {"FINISHED"}


class PAPERCRAFT_OT_build_rig(_NeedsModel, bpy.types.Operator):
    bl_idname = "papercraft.build_rig"
    bl_label = "Build Fold Rig"
    bl_description = (
        "Create (or rebuild) an armature with a driven hinge on every fold, so the "
        "sheet can fold up and fly onto the model. Rebuilding keeps your keyframes"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = _target(context)
        entry = live.get_or_build(context, obj)
        if entry is None:
            return {"CANCELLED"}
        arm, _ = build.build_rig(context, obj, entry)
        self.report({"INFO"}, f"Rig built with {len(arm.data.bones)} hinges")
        return {"FINISHED"}


class PAPERCRAFT_OT_remove_rig(_NeedsModel, bpy.types.Operator):
    bl_idname = "papercraft.remove_rig"
    bl_label = "Remove Fold Rig"
    bl_description = "Delete this model's fold rig and folding mesh"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = _target(context)
        return obj is not None and T.output(obj, T.ROLE_RIG) is not None

    def execute(self, context):
        obj = _target(context)
        saved = build.ensure_object_mode(context)
        build.remove_rig(obj)
        context.view_layer.objects.active = obj
        build.restore_mode(context, (obj.name, saved[1]))
        return {"FINISHED"}


class PAPERCRAFT_OT_animate(_NeedsModel, bpy.types.Operator):
    bl_idname = "papercraft.animate"
    bl_label = "Keyframe Fold Animation"
    bl_description = "Keyframe the rig: fold up from flat, then move onto the model"
    bl_options = {"REGISTER", "UNDO"}

    start: IntProperty(name="Start Frame", default=1)
    fold_frames: IntProperty(name="Fold Frames", default=72, min=1)
    assemble_frames: IntProperty(name="Assemble Frames", default=48, min=1)

    @classmethod
    def poll(cls, context):
        obj = _target(context)
        return obj is not None and T.output(obj, T.ROLE_RIG) is not None

    def invoke(self, context, event):
        self.start = context.scene.frame_start
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        arm = T.output(_target(context), T.ROLE_RIG)
        fold_end = self.start + self.fold_frames
        end = fold_end + self.assemble_frames
        build.keyframe_animation(arm, self.start, fold_end, end)
        scene = context.scene
        scene.frame_start = min(scene.frame_start, self.start)
        scene.frame_end = max(scene.frame_end, end)
        return {"FINISHED"}


class PAPERCRAFT_OT_export_svg(_NeedsModel, bpy.types.Operator, ExportHelper):
    bl_idname = "papercraft.export_svg"
    bl_label = "Export SVG Pages"
    bl_description = "Write one SVG per page (print, or send to a cutting machine)"

    filename_ext = ".svg"
    filter_glob: StringProperty(default="*.svg", options={"HIDDEN"})

    def draw(self, context):
        from .ui import draw_export

        obj = _target(context)
        if obj is not None:
            draw_export(self.layout, obj.papercraft)

    def invoke(self, context, event):
        src = _target(context)
        if src is not None and not self.filepath:
            blend_dir = (
                os.path.dirname(bpy.data.filepath) if bpy.data.filepath else os.path.expanduser("~")
            )
            self.filepath = os.path.join(blend_dir, f"{bpy.path.clean_name(src.name)}.svg")
        return super().invoke(context, event)

    def execute(self, context):
        obj = _target(context)
        entry = live.get_or_build(context, obj)
        if entry is None:
            return {"CANCELLED"}
        props = obj.papercraft
        docs = write_pages(context, obj, entry, self.filepath, self)
        if docs is None:
            return {"CANCELLED"}
        self.report({"INFO"}, f"Wrote {len(docs)} page(s) to {os.path.dirname(self.filepath)}")
        props.info_message = props.info_message  # keep UI in sync
        return {"FINISHED"}


SUFFIX = {"PRINT": "", "CUT": "_cut", "CREASE": "_crease", "LINES": "_lines"}


def svg_documents(context, obj, entry, reporter=None):
    props = obj.papercraft
    mode = props.texture_mode
    textures = uvs = page_images = None
    if props.export_content != "PRINT":
        mode = "WHITE"  # lines only: nothing to bake or embed
    if mode == "IMAGE":
        textures = textures_for(entry.src.images, props.texture_max)
        uvs = [entry.src.uv_layers.get(name) for name in entry.src.image_uvs]
    elif mode == "BAKE":
        from .bake import bake_pages

        wm = context.window_manager
        wm.progress_begin(0, max(entry.lay.page_count, 1))
        try:
            margin = props.tab_height + 1.0 if props.tab_texture else props.bleed + 0.2
            page_images = bake_pages(
                context, obj, entry,
                dpi=props.bake_dpi, bake_pass=props.bake_pass, margin_mm=margin,
                progress=wm.progress_update,
            )
        except RuntimeError as exc:
            if reporter is not None:
                reporter.report({"ERROR"}, f"Bake failed: {exc}")
            return None
        finally:
            wm.progress_end()
    return export_pages(
        entry.res,
        entry.lay,
        colors=entry.src.colors,
        fill_faces=mode != "WHITE",
        numbers=props.number_mode,
        title=obj.name,
        textures=textures,
        uvs=uvs,
        line_style=props.line_style,
        line_color=tuple(props.line_color),
        line_opacity=props.line_opacity,
        line_width=props.line_width,
        fold_min_angle=props.fold_min_angle,
        bleed=props.bleed,
        page_images=page_images,
        tab_texture=props.tab_texture and mode == "BAKE",
        content=props.export_content,
        dashed=props.dashed_folds,
    )


def write_pages(context, obj, entry, filepath, reporter=None):
    docs = svg_documents(context, obj, entry, reporter)
    if docs is None:
        return None
    base, ext = os.path.splitext(filepath)
    suffix = SUFFIX.get(obj.papercraft.export_content, "")
    if suffix and not base.endswith(suffix):
        base += suffix
        filepath = base + ext
    for n, doc in enumerate(docs, 1):
        path = filepath if len(docs) == 1 else f"{base}_page{n:02d}{ext}"
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(doc)
    return docs


classes = (
    PAPERCRAFT_OT_unfold,
    PAPERCRAFT_OT_clear,
    PAPERCRAFT_OT_frame_layout,
    PAPERCRAFT_OT_reset_layout,
    PAPERCRAFT_OT_build_rig,
    PAPERCRAFT_OT_remove_rig,
    PAPERCRAFT_OT_animate,
    PAPERCRAFT_OT_export_svg,
)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
