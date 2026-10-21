# SPDX-License-Identifier: GPL-3.0-or-later
"""Which model is being worked on, and the objects generated from it.

Every model keeps its own settings (Object.papercraft). Generated objects
(layout, rig, folding mesh, assembly targets) carry a role and a pointer back
to their model, so several models can be unfolded in one scene and objects
can be renamed freely.
"""

import bpy

ROLE_NONE = "NONE"
ROLE_SOURCE = "SOURCE"
ROLE_LAYOUT = "LAYOUT"
ROLE_RIG = "RIG"
ROLE_FOLDING = "FOLDING"
ROLE_TARGET = "TARGET"
GENERATED = {ROLE_LAYOUT, ROLE_RIG, ROLE_FOLDING, ROLE_TARGET}

ROOT_COLLECTION = "Papercraft"


def target(context):
    """The model the UI should act on: the active object, or the model a
    selected layout/rig belongs to."""
    obj = getattr(context, "active_object", None)
    if obj is None:
        return None
    pc = getattr(obj, "papercraft", None)
    if pc is None:
        return None
    if pc.role in GENERATED:
        src = pc.source_obj
        return src if src is not None and src.name in bpy.data.objects else None
    return obj if obj.type == "MESH" else None


def outputs(src, role):
    return [
        o for o in bpy.data.objects
        if o.papercraft.role == role and o.papercraft.source_obj == src
    ]


def output(src, role):
    found = outputs(src, role)
    return found[0] if found else None


def sources(scene):
    return [o for o in scene.objects if o.type == "MESH" and o.papercraft.role == ROLE_SOURCE]


def tag(obj, role, src):
    obj.papercraft.role = role
    obj.papercraft.source_obj = src


def collection_for(scene, src):
    """Papercraft / <model name>: holds everything generated for a model."""
    root = bpy.data.collections.get(ROOT_COLLECTION)
    if root is None:
        root = bpy.data.collections.new(ROOT_COLLECTION)
    if root.name not in scene.collection.children:
        scene.collection.children.link(root)
    for child in root.children:
        if child.get("papercraft_source") == src.name_full:
            return child
    # Reuse whatever collection already holds this model's layout.
    lay = output(src, ROLE_LAYOUT)
    if lay is not None:
        for coll in lay.users_collection:
            if coll.name in root.children:
                coll["papercraft_source"] = src.name_full
                return coll
    coll = bpy.data.collections.new(f"Papercraft {src.name}")
    coll["papercraft_source"] = src.name_full
    root.children.link(coll)
    return coll
