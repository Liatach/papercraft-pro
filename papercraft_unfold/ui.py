# SPDX-License-Identifier: GPL-3.0-or-later
import bpy


class PAPERCRAFT_PT_main(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Papercraft"
    bl_label = "Papercraft Unfold"

    def draw(self, context):
        layout = self.layout
        props = context.scene.papercraft
        row = layout.row(align=True)
        row.prop(props, "source", text="")
        row.operator("papercraft.use_active", text="", icon="EYEDROPPER")

        row = layout.row(align=True)
        row.scale_y = 1.4
        row.operator("papercraft.unfold", icon="MOD_UVPROJECT")
        row.prop(props, "live", text="", icon="FILE_REFRESH", toggle=True)
        layout.operator("papercraft.frame_layout", icon="VIEW_ORTHO")

        if props.info_islands:
            box = layout.box()
            col = box.column(align=True)
            col.label(text=f"{props.info_islands} piece(s), {props.info_glue} glue edges")
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


class PAPERCRAFT_PT_paper(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Papercraft"
    bl_label = "Paper & Scale"
    bl_parent_id = "PAPERCRAFT_PT_main"

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        props = context.scene.papercraft
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


class PAPERCRAFT_PT_pieces(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Papercraft"
    bl_label = "Pieces & Tabs"
    bl_parent_id = "PAPERCRAFT_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        props = context.scene.papercraft
        col = layout.column()
        col.prop(props, "priority")
        col.prop(props, "max_faces")
        col.separator()
        col.prop(props, "tab_height")
        col.prop(props, "tab_angle")


class PAPERCRAFT_PT_animate(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Papercraft"
    bl_label = "Fold Animation"
    bl_parent_id = "PAPERCRAFT_PT_main"

    def draw(self, context):
        layout = self.layout
        layout.operator("papercraft.build_rig", icon="ARMATURE_DATA")
        arm = bpy.data.objects.get("Papercraft Rig")
        if arm is not None and "fold" in arm:
            col = layout.column(align=True)
            col.prop(arm, '["fold"]', text="Fold", slider=True)
            col.prop(arm, '["assemble"]', text="Assemble", slider=True)
            layout.operator("papercraft.animate", icon="KEY_HLT")


class PAPERCRAFT_PT_export(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Papercraft"
    bl_label = "Export"
    bl_parent_id = "PAPERCRAFT_PT_main"

    def draw(self, context):
        layout = self.layout
        props = context.scene.papercraft
        col = layout.column(align=True)
        col.prop(props, "fill_faces")
        col.prop(props, "numbers")
        layout.operator("papercraft.export_svg", icon="EXPORT")


classes = (
    PAPERCRAFT_PT_main,
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
