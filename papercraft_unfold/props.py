# SPDX-License-Identifier: GPL-3.0-or-later
import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)

from .core.layout import PAGE_SIZES_MM


def _poll_mesh(self, obj):
    return obj.type == "MESH" and not obj.name.startswith("Papercraft ")


def _changed(self, context):
    from . import live

    if self.live:
        live.schedule()


def _live_toggled(self, context):
    from . import live

    if self.live:
        live.schedule(force=True)


PAPER_ITEMS = [(k, k.title() if len(k) > 3 else k, f"{w:g} x {h:g} mm") for k, (w, h) in PAGE_SIZES_MM.items()]
PAPER_ITEMS.append(("CUSTOM", "Custom", "Custom page size"))


class PapercraftSettings(bpy.types.PropertyGroup):
    source: PointerProperty(
        name="Model",
        type=bpy.types.Object,
        poll=_poll_mesh,
        update=_changed,
        description="Mesh to unfold",
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
    max_faces: IntProperty(
        name="Max Faces per Piece",
        default=0,
        min=0,
        update=_changed,
        description="Split pieces larger than this (0 = no limit). Smaller pieces "
        "leave more room for tabs on round models",
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
    fill_faces: BoolProperty(
        name="Print Face Colours", default=True,
        description="Fill pieces with their material colour (turn off for cut-only files)",
    )
    numbers: BoolProperty(name="Edge Numbers", default=True)

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
            self.priority, self.max_faces, self.page_size(), self.margin, self.spacing,
            self.scale_mode, self.pages, self.model_size_mm, self.ratio,
            self.tab_height, self.tab_angle,
        )


def register():
    bpy.utils.register_class(PapercraftSettings)
    bpy.types.Scene.papercraft = PointerProperty(type=PapercraftSettings)


def unregister():
    del bpy.types.Scene.papercraft
    bpy.utils.unregister_class(PapercraftSettings)
