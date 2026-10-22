# SPDX-License-Identifier: GPL-3.0-or-later
import bpy

from . import build, live
from . import targets as T


class _Base:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Papercraft"


class _Child(_Base):
    bl_parent_id = "PAPERCRAFT_PT_main"

    @classmethod
    def poll(cls, context):
        return T.target(context) is not None


class PAPERCRAFT_PT_main(_Base, bpy.types.Panel):
    bl_label = "Papercraft Unfold"

    def draw(self, context):
        layout = self.layout
        obj = T.target(context)
        if obj is None:
            layout.label(text="Select a mesh to unfold", icon="INFO")
            others = T.sources(context.scene)
            if others:
                layout.label(text=f"{len(others)} model(s) unfolded in this scene")
            return
        props = obj.papercraft
        box = layout.box()
        row = box.row()
        row.label(text=obj.name, icon="MESH_DATA")
        if props.role == T.ROLE_SOURCE:
            row.operator("papercraft.clear", text="", icon="TRASH")

        row = layout.row(align=True)
        row.scale_y = 1.4
        row.operator("papercraft.unfold", icon="MOD_UVPROJECT")
        row.prop(props, "live", text="", icon="FILE_REFRESH", toggle=True)
        row = layout.row(align=True)
        row.operator("papercraft.frame_layout", icon="VIEW_ORTHO")
        row.operator("papercraft.edit_cuts", icon="SCULPTMODE_HLT")

        if props.role == T.ROLE_SOURCE and props.info_islands:
            box = layout.box()
            col = box.column(align=True)
            col.label(text=f"{props.info_islands} piece(s), {props.info_glue} glue edges")
            entry = live.cached(obj)
            if entry is not None and props.priority == "auto" and entry.res.priority:
                col.label(text="Auto chose: " + (
                    "Fewest Pieces" if entry.res.priority == "long" else "Seams on Creases"))
            icon = "CHECKMARK" if props.info_fits else "ERROR"
            col.label(text=f"{props.info_pages} page(s)", icon=icon)
            col.label(text=f"Model size {props.info_size_mm:.0f} mm")
            col.label(text=f"1 unit = {props.info_scale:.2f} mm")
        if props.info_message:
            box = layout.box()
            for part in props.info_message.split("; "):
                box.label(text=part, icon="INFO")

        if context.mode == "EDIT_MESH":
            box = layout.box()
            box.label(text="Cuts (select edges)")
            row = box.row(align=True)
            row.operator("mesh.mark_seam", text="Cut Here").clear = False
            row.operator("mesh.mark_seam", text="Join").clear = True
            if not props.live:
                box.label(text="Turn on Live Layout to see changes", icon="INFO")


class PAPERCRAFT_PT_cuts(_Child, bpy.types.Panel):
    bl_label = "Cut Along"

    def draw(self, context):
        layout = self.layout
        props = T.target(context).papercraft
        col = layout.column(align=True)
        col.prop(props, "cut_seams")
        col.prop(props, "cut_materials")
        col.prop(props, "cut_colors")
        col.prop(props, "cut_sharp")
        box = layout.box()
        col = box.column(align=True)
        col.operator("papercraft.edit_cuts", text="Edit Cuts & Tabs", icon="SCULPTMODE_HLT")
        col.label(text="Click edge: cut / join")
        col.label(text="Click tab: other side of seam")
        col.label(text="Shift+click tab: inside / outside")


class PAPERCRAFT_PT_paper(_Child, bpy.types.Panel):
    bl_label = "Paper & Scale"

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        obj = T.target(context)
        props = obj.papercraft
        col = layout.column()
        col.prop(props, "paper")
        if props.paper == "CUSTOM":
            sub = col.column(align=True)
            sub.prop(props, "custom_w")
            sub.prop(props, "custom_h")
        col.prop(props, "landscape")
        col.prop(props, "margin")
        col.prop(props, "spacing")
        col.separator()
        col.prop(props, "scale_mode")
        if props.scale_mode == "FIT":
            col.prop(props, "pages")
        elif props.scale_mode == "HEIGHT":
            col.prop(props, "model_size_mm")
        else:
            col.prop(props, "ratio")
        if T.output(obj, T.ROLE_LAYOUT) is not None:
            layout.operator("papercraft.reset_layout", icon="LOOP_BACK")


class PAPERCRAFT_PT_pieces(_Child, bpy.types.Panel):
    bl_label = "Pieces & Tabs"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        props = T.target(context).papercraft
        col = layout.column()
        col.prop(props, "priority")
        col.prop(props, "valley_bias")
        col.prop(props, "max_faces")
        col.separator()
        col.prop(props, "tab_height")
        col.prop(props, "tab_angle")


class PAPERCRAFT_PT_animate(_Child, bpy.types.Panel):
    bl_label = "Fold Animation"

    def draw(self, context):
        layout = self.layout
        obj = T.target(context)
        arm = T.output(obj, T.ROLE_RIG)
        if arm is None:
            layout.operator("papercraft.build_rig", icon="ARMATURE_DATA")
            return
        entry = live.cached(obj)
        if build.rig_is_stale(obj, entry):
            box = layout.box()
            box.alert = True
            box.label(text="Rig is out of date with the cuts", icon="ERROR")
        row = layout.row(align=True)
        row.operator("papercraft.build_rig", text="Rebuild Rig", icon="FILE_REFRESH")
        row.operator("papercraft.remove_rig", text="", icon="TRASH")
        if "fold" in arm:
            col = layout.column(align=True)
            col.prop(arm, '["fold"]', text="Fold", slider=True)
            col.prop(arm, '["assemble"]', text="Assemble", slider=True)
            layout.operator("papercraft.animate", icon="KEY_HLT")


def draw_export(layout, props):
    """Export settings (sidebar panel and the export file dialog)."""
    layout.use_property_split = True
    layout.use_property_decorate = False
    col = layout.column()
    col.prop(props, "export_content")
    if props.export_content != "PRINT":
        col.prop(props, "line_width")
        col.prop(props, "dashed_folds")
        col.prop(props, "fold_min_angle")
        if props.dashed_folds:
            col.label(text="Cutters score each dash separately", icon="INFO")
        if props.export_content == "LINES":
            col.label(text="Creases are red, cuts black", icon="INFO")
        return
    col.prop(props, "texture_mode")
    if props.texture_mode == "BAKE":
        col.prop(props, "bake_pass")
        col.prop(props, "bake_dpi")
        col.prop(props, "tab_texture")
    elif props.texture_mode == "IMAGE":
        col.prop(props, "texture_max")
    if props.texture_mode != "WHITE":
        col.prop(props, "bleed")
    col.separator()
    col.prop(props, "number_mode")
    col.separator()
    col.prop(props, "line_style")
    if props.line_style == "CUSTOM":
        col.prop(props, "line_color")
        col.prop(props, "line_opacity")
    sub = col.column()
    sub.active = props.line_style != "NONE"
    sub.prop(props, "line_width")
    sub.prop(props, "dashed_folds")
    sub.prop(props, "fold_min_angle")


class PAPERCRAFT_PT_export(_Child, bpy.types.Panel):
    bl_label = "Print & Export"

    def draw(self, context):
        draw_export(self.layout, T.target(context).papercraft)
        self.layout.operator("papercraft.export_svg", icon="EXPORT")


classes = (
    PAPERCRAFT_PT_main,
    PAPERCRAFT_PT_cuts,
    PAPERCRAFT_PT_paper,
    PAPERCRAFT_PT_pieces,
    PAPERCRAFT_PT_animate,
    PAPERCRAFT_PT_export,
)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
