# SPDX-License-Identifier: GPL-3.0-or-later
import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)

from .core.layout import PAGE_SIZES_MM


def _changed(self, context):
    from . import live

    if self.live and self.role == "SOURCE":
        live.schedule(self.id_data)


def _live_toggled(self, context):
    from . import live

    if self.live and self.role == "SOURCE":
        live.schedule(self.id_data, force=True)


ROLE_ITEMS = [
    ("NONE", "None", ""),
    ("SOURCE", "Model", "A model being unfolded"),
    ("LAYOUT", "Layout", "Paper layout generated from a model"),
    ("RIG", "Rig", "Fold rig generated from a model"),
    ("FOLDING", "Folding Mesh", "Mesh deformed by the fold rig"),
    ("TARGET", "Assembly Target", "Where a piece lands when assembled"),
]


PAPER_ITEMS = [(k, k.title() if len(k) > 3 else k, f"{w:g} x {h:g} mm") for k, (w, h) in PAGE_SIZES_MM.items()]
PAPER_ITEMS.append(("CUSTOM", "Custom", "Custom page size"))


class PapercraftSettings(bpy.types.PropertyGroup):
    """Per-object settings: each model keeps its own papercraft setup."""

    role: EnumProperty(name="Role", items=ROLE_ITEMS, default="NONE")
    source_obj: PointerProperty(
        name="Model", type=bpy.types.Object,
        description="Model this generated object belongs to",
    )
    live: BoolProperty(
        name="Live Layout",
        default=False,
        update=_live_toggled,
        description="Re-unfold automatically while you edit the model or mark seams",
    )

    # Unfolding --------------------------------------------------------
    priority: EnumProperty(
        name="Keep Together",
        items=[
            ("flat", "Flat Areas", "Fold flattest edges first; cuts land on sharp edges"),
            ("long", "Long Edges", "Fold longest edges first; fewer cuts, longer pieces"),
        ],
        default="flat",
        update=_changed,
    )
    valley_bias: FloatProperty(
        name="Prefer Valley Cuts", default=0.5, min=0.0, max=1.0, subtype="FACTOR",
        update=_changed,
        description="Put cuts on valley (inward) edges where possible: seams hide in "
        "the creases and mountain ridges stay crisp. 0 = cut wherever is sharpest",
    )
    max_faces: IntProperty(
        name="Max Faces per Piece",
        default=0,
        min=0,
        update=_changed,
        description="Split pieces larger than this (0 = no limit). Smaller pieces "
        "leave more room for tabs on round models",
    )

    # Cuts ---------------------------------------------------------------
    cut_seams: BoolProperty(
        name="Seams", default=True, update=_changed,
        description="Cut along edges marked as seams (and edges cut with the Edit Cuts tool)",
    )
    cut_materials: BoolProperty(
        name="Material Boundaries", default=False, update=_changed,
        description="Cut wherever the material changes, so each material becomes its own piece",
    )
    cut_colors: BoolProperty(
        name="Colour Boundaries", default=False, update=_changed,
        description="Cut wherever the painted colour (active colour attribute) changes. "
        "Paint regions in Vertex Paint mode to define the pieces",
    )
    cut_sharp: BoolProperty(
        name="Sharp Edges", default=False, update=_changed,
        description="Cut along edges marked Sharp",
    )

    # Paper --------------------------------------------------------------
    paper: EnumProperty(name="Paper", items=PAPER_ITEMS, default="A4", update=_changed)
    landscape: BoolProperty(name="Landscape", default=False, update=_changed)
    custom_w: FloatProperty(name="Width", default=210.0, min=20.0, unit="NONE", update=_changed)
    custom_h: FloatProperty(name="Height", default=297.0, min=20.0, update=_changed)
    margin: FloatProperty(
        name="Margin", default=10.0, min=0.0, max=60.0, update=_changed,
        description="Unprintable border in mm",
    )
    spacing: FloatProperty(
        name="Spacing", default=3.0, min=0.0, max=30.0, update=_changed,
        description="Gap between pieces in mm",
    )

    # Scale --------------------------------------------------------------
    scale_mode: EnumProperty(
        name="Scale",
        items=[
            ("FIT", "Fit to Pages", "Largest model that fits on the chosen number of pages"),
            ("HEIGHT", "Model Size", "Model's largest dimension in millimetres"),
            ("RATIO", "Fixed Ratio", "Millimetres of paper per Blender unit"),
        ],
        default="FIT",
        update=_changed,
    )
    pages: IntProperty(name="Pages", default=1, min=1, max=200, update=_changed)
    model_size_mm: FloatProperty(name="Size (mm)", default=150.0, min=5.0, update=_changed)
    ratio: FloatProperty(name="mm per Unit", default=100.0, min=0.001, update=_changed)

    # Tabs ---------------------------------------------------------------
    tab_height: FloatProperty(name="Tab Height", default=5.0, min=0.5, max=30.0, update=_changed)
    tab_angle: FloatProperty(name="Tab Angle", default=45.0, min=15.0, max=89.0, update=_changed)

    # Export -------------------------------------------------------------
    texture_mode: EnumProperty(
        name="Print",
        items=[
            ("BAKE", "Baked Material",
             "Bake what the material shows onto each page with Cycles: works with "
             "layered/painted materials (e.g. Paint System), procedurals and vertex colours"),
            ("IMAGE", "Image Textures", "Print image textures directly through the UVs (fast)"),
            ("COLOR", "Flat Colours", "Each piece in its material's base colour"),
            ("WHITE", "Lines Only", "No fill: white paper with lines (cutting machines)"),
        ],
        default="BAKE",
    )
    bake_pass: EnumProperty(
        name="Bake",
        items=[
            ("BASE", "Base Colour", "The material's colour without lighting (usual for papercraft)"),
            ("EMIT", "Emission", "For materials that output colour through an Emission shader"),
            ("COMBINED", "Full Render", "Colour with the scene's lighting and shading"),
        ],
        default="BASE",
    )
    bake_dpi: IntProperty(
        name="Bake DPI", default=200, min=50, max=600,
        description="Print resolution of the baked pages (300 for fine detail; slower)",
    )
    tab_texture: BoolProperty(
        name="Texture on Tabs", default=False,
        description="Continue the neighbouring face's art onto the tabs, so small gaps "
        "at glued seams don't show the tab colour (baked mode)",
    )
    number_mode: EnumProperty(
        name="Numbers",
        items=[
            ("FACES", "On Faces", "Matching numbers just inside both glued edges"),
            ("CLEAN", "Tabs & Scrap",
             "Number on the tab, and on the scrap just outside the other edge: "
             "nothing printed on the art"),
            ("OFF", "Off", "No glue numbers"),
        ],
        default="FACES",
    )
    texture_max: IntProperty(
        name="Max Texture Size", default=2048, min=128, max=16384,
        description="Larger images are downsampled to this many pixels when embedded",
    )
    bleed: FloatProperty(
        name="Bleed", default=0.2, min=0.0, max=3.0,
        description="Millimetres of colour printed past each edge so slightly "
        "off cuts don't show white",
    )
    line_style: EnumProperty(
        name="Lines",
        items=[
            ("DEFAULT", "Default", "Black cuts, red mountain folds, blue valley folds"),
            ("CUSTOM", "Custom Colour",
             "All lines in one colour and opacity, e.g. white over dark art or soft black over light art"),
            ("NONE", "No Lines", "Print the art only (lines stay in the file as hidden layers)"),
        ],
        default="DEFAULT",
    )
    line_color: FloatVectorProperty(
        name="Line Colour", subtype="COLOR", size=3, min=0.0, max=1.0,
        default=(0.0, 0.0, 0.0),
    )
    line_opacity: FloatProperty(
        name="Line Opacity", default=0.5, min=0.0, max=1.0, subtype="FACTOR",
    )
    line_width: FloatProperty(name="Line Width", default=0.25, min=0.05, max=2.0)
    fold_min_angle: FloatProperty(
        name="Hide Folds Under", default=1.0, min=0.0, max=45.0,
        description="Don't draw fold lines flatter than this many degrees",
    )

    # Read-only info -----------------------------------------------------
    info_islands: IntProperty(default=0)
    info_pages: IntProperty(default=0)
    info_scale: FloatProperty(default=0.0)
    info_size_mm: FloatProperty(default=0.0)
    info_glue: IntProperty(default=0)
    info_message: StringProperty(default="")
    info_fits: BoolProperty(default=True)

    def page_size(self):
        if self.paper == "CUSTOM":
            w, h = self.custom_w, self.custom_h
        else:
            w, h = PAGE_SIZES_MM[self.paper]
        return (h, w) if self.landscape else (w, h)

    def settings_key(self):
        return (
            self.priority, self.valley_bias, self.max_faces, self.page_size(), self.margin, self.spacing,
            self.scale_mode, self.pages, self.model_size_mm, self.ratio,
            self.tab_height, self.tab_angle,
            self.cut_seams, self.cut_materials, self.cut_colors, self.cut_sharp,
        )

    def cut_rules(self):
        return {
            "seams": self.cut_seams,
            "materials": self.cut_materials,
            "colors": self.cut_colors,
            "sharp": self.cut_sharp,
        }


def register():
    bpy.utils.register_class(PapercraftSettings)
    bpy.types.Object.papercraft = PointerProperty(type=PapercraftSettings)


def unregister():
    del bpy.types.Object.papercraft
    bpy.utils.unregister_class(PapercraftSettings)
