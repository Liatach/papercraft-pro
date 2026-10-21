# SPDX-License-Identifier: GPL-3.0-or-later
import os

import bpy
from bpy.props import IntProperty, StringProperty
from bpy_extras.io_utils import ExportHelper

from . import build, live
from .core.svg import export_pages


def _need_source(context):
    props = context.scene.papercraft
    return props.source is not None


class PAPERCRAFT_OT_use_active(bpy.types.Operator):
    bl_idname = "papercraft.use_active"
    bl_label = "Use Active Object"
    bl_description = "Unfold the active mesh object"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == "MESH" and not obj.name.startswith("Papercraft ")

    def execute(self, context):
        context.scene.papercraft.source = context.active_object
        return {"FINISHED"}


class PAPERCRAFT_OT_unfold(bpy.types.Operator):
    bl_idname = "papercraft.unfold"
    bl_label = "Unfold"
    bl_description = "Unfold the model and lay the pieces out on pages beside it"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _need_source(context)

    def execute(self, context):
        entry = live.get_or_build(context, force=True)
        if entry is None:
            self.report({"WARNING"}, context.scene.papercraft.info_message)
            return {"CANCELLED"}
        props = context.scene.papercraft
        self.report(
            {"INFO"},
            f"{len(entry.res.islands)} piece(s) on {entry.lay.page_count} page(s), "
            f"model {props.info_size_mm:.0f} mm",
        )
        return {"FINISHED"}


class PAPERCRAFT_OT_frame_layout(bpy.types.Operator):
    bl_idname = "papercraft.frame_layout"
    bl_label = "Show Layout"
    bl_description = "Look straight down at the paper layout in this viewport"

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == "VIEW_3D" and (
            bpy.data.objects.get("Papercraft Layout") is not None
        )

    def execute(self, context):
        obj = bpy.data.objects["Papercraft Layout"]
        if context.mode != "OBJECT":
            self.report({"INFO"}, "Switch to Object Mode to frame the layout")
            return {"CANCELLED"}
        for o in context.selected_objects:
            o.select_set(False)
        obj.hide_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        bpy.ops.view3d.view_axis(type="TOP")
        bpy.ops.view3d.view_selected()
        return {"FINISHED"}


class PAPERCRAFT_OT_build_rig(bpy.types.Operator):
    bl_idname = "papercraft.build_rig"
    bl_label = "Build Fold Rig"
    bl_description = (
        "Create an armature with a driven hinge on every fold, so the sheet "
        "can fold up and fly onto the model"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _need_source(context)

    def execute(self, context):
        entry = live.get_or_build(context)
        if entry is None:
            return {"CANCELLED"}
        arm, _ = build.build_rig(context, entry.src, entry.res, entry.lay)
        self.report({"INFO"}, f"Rig built with {len(arm.data.bones)} hinges")
        return {"FINISHED"}


class PAPERCRAFT_OT_animate(bpy.types.Operator):
    bl_idname = "papercraft.animate"
    bl_label = "Keyframe Fold Animation"
    bl_description = "Keyframe the rig: fold up from flat, then move onto the model"
    bl_options = {"REGISTER", "UNDO"}

    start: IntProperty(name="Start Frame", default=1)
    fold_frames: IntProperty(name="Fold Frames", default=72, min=1)
    assemble_frames: IntProperty(name="Assemble Frames", default=48, min=1)

    @classmethod
    def poll(cls, context):
        return bpy.data.objects.get("Papercraft Rig") is not None

    def invoke(self, context, event):
        self.start = context.scene.frame_start
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        arm = bpy.data.objects["Papercraft Rig"]
        fold_end = self.start + self.fold_frames
        end = fold_end + self.assemble_frames
        build.keyframe_animation(arm, self.start, fold_end, end)
        scene = context.scene
        scene.frame_start = min(scene.frame_start, self.start)
        scene.frame_end = max(scene.frame_end, end)
        return {"FINISHED"}


class PAPERCRAFT_OT_export_svg(bpy.types.Operator, ExportHelper):
    bl_idname = "papercraft.export_svg"
    bl_label = "Export SVG Pages"
    bl_description = "Write one SVG per page (print, or send to a cutting machine)"

    filename_ext = ".svg"
    filter_glob: StringProperty(default="*.svg", options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return _need_source(context)

    def invoke(self, context, event):
        src = context.scene.papercraft.source
        if src is not None and not self.filepath:
            blend_dir = os.path.dirname(bpy.data.filepath) if bpy.data.filepath else os.path.expanduser("~")
            self.filepath = os.path.join(blend_dir, f"{bpy.path.clean_name(src.name)}.svg")
        return super().invoke(context, event)

    def execute(self, context):
        entry = live.get_or_build(context)
        if entry is None:
            return {"CANCELLED"}
        props = context.scene.papercraft
        docs = export_pages(
            entry.res,
            entry.lay,
            colors=entry.src.colors,
            fill_faces=props.fill_faces,
            numbers=props.numbers,
            title=props.source.name,
        )
        base, ext = os.path.splitext(self.filepath)
        written = []
        for n, doc in enumerate(docs, 1):
            path = self.filepath if len(docs) == 1 else f"{base}_page{n:02d}{ext}"
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(doc)
            written.append(path)
        self.report({"INFO"}, f"Wrote {len(written)} page(s) to {os.path.dirname(self.filepath)}")
        return {"FINISHED"}


classes = (
    PAPERCRAFT_OT_use_active,
    PAPERCRAFT_OT_unfold,
    PAPERCRAFT_OT_frame_layout,
    PAPERCRAFT_OT_build_rig,
    PAPERCRAFT_OT_animate,
    PAPERCRAFT_OT_export_svg,
)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
