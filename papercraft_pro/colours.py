# SPDX-License-Identifier: GPL-3.0-or-later
"""True colours while painting (ported from Pop-Up Pro).

What the viewport shows is the paint after lighting and the scene's view
transform (AgX by default, which dulls bright, saturated colours). Picking a
colour off the screen (the eyedropper, or sampling) then reads back a
darker, duller colour than the one painted, and each re-sample drifts
further. While you're in Texture Paint on a papercraft model this:

* switches the scene's view transform to Standard,
* shows the paint unlit: a material whose surface comes straight from a
  Principled BSDF is switched to its Base Color as emission (alpha kept);
  materials drawn some other way (layer stacks, your own
  nodes) are left alone, as they would lose their layers, and
* shows Solid-mode 3D views with flat texture colours,

so what you see and sample is what's in the paint, and what prints. All of
it is put back on leaving Texture Paint, and anything a crash left behind
is tidied when a file opens.
"""

import json

import bpy
from bpy.app.handlers import persistent

from . import targets as T

KEY = "papercraft_true_colours"
EMIT, MIX, CLEAR = "Papercraft True Colour", "Papercraft True Mix", "Papercraft True Clear"
OURS = (EMIT, MIX, CLEAR)


def _views():
    wm = bpy.context.window_manager
    if wm is None:
        return
    for wi, win in enumerate(wm.windows):
        for ai, area in enumerate(win.screen.areas):
            if area.type == "VIEW_3D":
                yield f"{wi}:{ai}", area.spaces.active.shading


def _output(nt):
    return next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output),
                None)


def _unlit(mat):
    """Point the material output at its Base Color as emission. Returns the
    socket the output was fed from (to put back), or None if left alone."""
    if mat is None or not mat.use_nodes or mat.node_tree is None:
        return None
    nt = mat.node_tree
    if any(nt.nodes.get(k) for k in OURS):
        return None  # already unlit
    out = _output(nt)
    if out is None or not out.inputs["Surface"].links:
        return None
    was = out.inputs["Surface"].links[0].from_socket
    bsdf = was.node
    if bsdf.type != "BSDF_PRINCIPLED":
        return None  # layer stacks and custom nodes: leave them be
    emit = nt.nodes.new("ShaderNodeEmission")
    mix = nt.nodes.new("ShaderNodeMixShader")
    clear = nt.nodes.new("ShaderNodeBsdfTransparent")
    for node, name in ((emit, EMIT), (mix, MIX), (clear, CLEAR)):
        node.name = node.label = name
    for i, n in enumerate((emit, clear, mix)):
        n.location = out.location.x - 200, out.location.y - 220 - 120 * i
    base = bsdf.inputs["Base Color"]
    if base.links:
        nt.links.new(base.links[0].from_socket, emit.inputs["Color"])
    else:
        emit.inputs["Color"].default_value = base.default_value
    emit.inputs["Strength"].default_value = 1.0
    alpha = bsdf.inputs["Alpha"]
    if alpha.links:
        nt.links.new(alpha.links[0].from_socket, mix.inputs["Fac"])
    else:
        mix.inputs["Fac"].default_value = alpha.default_value
    nt.links.new(clear.outputs[0], mix.inputs[1])
    nt.links.new(emit.outputs[0], mix.inputs[2])
    nt.links.new(mix.outputs[0], out.inputs["Surface"])
    return was


def _relit(mat, was=None):
    """Put the material's own surface back and remove our nodes."""
    if mat is None or mat.node_tree is None:
        return
    nt = mat.node_tree
    ours = [nt.nodes.get(k) for k in OURS]
    ours = [n for n in ours if n is not None]
    if not ours:
        return
    out = _output(nt)
    lit = was
    if lit is None:
        # No record (a crash): the Principled BSDF whose colour we showed.
        emit = nt.nodes.get(EMIT)
        src = emit.inputs["Color"].links[0].from_node if emit and emit.inputs["Color"].links else None
        bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"
                     and (src is None or any(ln.from_node == src
                                             for ln in n.inputs["Base Color"].links))), None)
        lit = bsdf.outputs[0] if bsdf is not None else None
    if out is not None and lit is not None:
        links = out.inputs["Surface"].links
        if not links or links[0].from_node in ours:
            nt.links.new(lit, out.inputs["Surface"])
    for n in ours:
        nt.nodes.remove(n)


def _materials(obj):
    return [s.material for s in obj.material_slots if s.material is not None] if obj else []


def enter(scene, obj):
    if KEY in scene or obj is None:
        return False
    vs = scene.view_settings
    saved = dict(view=vs.view_transform, look=vs.look, shading={}, mats=[])
    for key, sh in _views():
        if sh.type == "SOLID":
            saved["shading"][key] = (sh.light, sh.color_type)
            sh.light = "FLAT"
            sh.color_type = "TEXTURE"
    for mat in _materials(obj):
        was = _unlit(mat)
        if was is not None:
            saved["mats"].append([mat.name, was.node.name, was.identifier])
    scene[KEY] = json.dumps(saved)
    try:
        vs.view_transform = "Standard"
        vs.look = "None"
    except TypeError:
        pass
    return True


def leave(scene):
    raw = scene.get(KEY)
    if raw is None:
        return False
    del scene[KEY]
    try:
        saved = json.loads(raw)
    except ValueError:
        return False
    vs = scene.view_settings
    try:
        vs.view_transform = saved["view"]
        vs.look = saved["look"]
    except (TypeError, KeyError):
        pass
    for key, sh in _views():
        old = saved.get("shading", {}).get(key)
        if old is not None and sh.type == "SOLID":
            try:
                sh.light, sh.color_type = old
            except TypeError:
                pass
    for name, node_name, ident in saved.get("mats", []):
        mat = bpy.data.materials.get(name)
        if mat is None or mat.node_tree is None:
            continue
        node = mat.node_tree.nodes.get(node_name)
        was = next((s for s in node.outputs if s.identifier == ident), None) if node else None
        _relit(mat, was)
    return True


def heal():
    """Not painting, yet an unlit preview is still in a material (a session
    that ended oddly): put the lit material back."""
    fixed = 0
    for mat in bpy.data.materials:
        if mat.node_tree is not None and any(mat.node_tree.nodes.get(k) for k in OURS):
            _relit(mat)
            fixed += 1
    return fixed


_last = {"mode": None}
_owner = object()


def _painted_model(context):
    """The papercraft model being painted (the model itself, or its layout
    or rig, which share its materials), if True Colours is on for it."""
    obj = T.target(context)
    if obj is None or not obj.papercraft.true_colours:
        return None
    return obj


def _mode_changed():
    try:
        ctx = bpy.context
        mode = ctx.mode
        was = _last["mode"]
        _last["mode"] = mode
        scene = ctx.scene
        if scene is None or mode == was:
            return
        if mode == "PAINT_TEXTURE":
            model = _painted_model(ctx)
            if model is not None:
                enter(scene, model)
        elif was == "PAINT_TEXTURE":
            leave(scene)
    except Exception as exc:  # never get in the way of painting
        print(f"Papercraft Pro: true colours: {exc}")


@persistent
def subscribe(*_args):
    bpy.msgbus.clear_by_owner(_owner)
    bpy.msgbus.subscribe_rna(key=(bpy.types.Object, "mode"), owner=_owner, args=(),
                             notify=_mode_changed)
    # A file just opened: no painting preview left behind.
    try:
        scenes = list(bpy.data.scenes)
    except AttributeError:
        return
    painting = getattr(bpy.context, "mode", None) == "PAINT_TEXTURE"
    for sc in scenes:
        if KEY in sc and not painting:
            leave(sc)
    if not painting:
        heal()


def register():
    if subscribe not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(subscribe)
    subscribe()


def unregister():
    bpy.msgbus.clear_by_owner(_owner)
    if subscribe in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(subscribe)
