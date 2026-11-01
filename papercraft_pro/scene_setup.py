# SPDX-License-Identifier: GPL-3.0-or-later
"""Set Up Scene and render looks for papercraft renders.

Ported from Pop-Up Pro's scene setup. Light, sharp and easy to read:
Material Preview lit by Blender's dawn HDRI, a sun lamp for crisp shadows,
faint fold lines where faces meet at an angle, and EEVEE's cheap
screen-space ambient occlusion in the creases. Shadows and creases are
what tell a valley from a mountain fold at a glance.

Render looks (finished renders only; painting, baking and printing never
use them, and the viewport keeps its own light):
  CLEAN     even white light, no sun
  SHOWCASE  white light plus a key sun's shadows
  DAWN      Blender's sunrise HDRI with the sun's shadows
  STUDIO    Blender's studio HDRI with the sun: real reflections
  OWN       your own world and lights
Each lights with one thing but shows the camera a soft grey background, so
white paper doesn't vanish against white.
"""

import math
import os

import bpy
from mathutils import Vector

from . import targets as T

SUN = "Papercraft Sun"
KEY = "Papercraft Key Light"
CAMERA = "Papercraft Camera"
GRAIN = "Papercraft Paper Texture"
WHITE = "Papercraft White"
DAWN = "Papercraft Dawn"
SHOW = "Papercraft Showcase"
STUDIO = "Papercraft Studio"
OURS = (WHITE, DAWN, SHOW, STUDIO)

LOOKS = [
    ("CLEAN", "Clean", "Even white light, no sun: plain, flat images of the model"),
    ("SHOWCASE", "Showcase", "White light and a sun's shadows, at 64 render samples: every "
     "fold reads at a glance"),
    ("DAWN", "Dawn", "Blender's sunrise light, as in Material Preview (warm where the low sun "
     "strikes, blue where the sky does) with the sun's shadows, at 64 samples"),
    ("STUDIO", "Studio", "A photo studio's light (Blender's studio HDRI) with the sun's "
     "shadows, at 64 samples: soft, even light with real reflections"),
    ("OWN", "Your Own", "Renders use your own world and lights; the papercraft sun stays in "
     "the viewport only"),
]


def _set(obj, attr, value):
    """Settings move between Blender versions: set what exists."""
    try:
        setattr(obj, attr, value)
    except (AttributeError, TypeError, ValueError):
        pass


def _bounds(objs):
    pts = []
    for o in objs:
        if o is None or o.type != "MESH":
            continue
        pts.extend(o.matrix_world @ Vector(c) for c in o.bound_box)
    if not pts:
        return Vector((0, 0, 0)), 1.0
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return (lo + hi) / 2, max((hi - lo).length / 2, 1e-3)


def subjects(model, frame="MODEL"):
    """What the camera frames: the model, or the model plus its layout and rig."""
    out = [model]
    if frame == "ALL":
        for role in (T.ROLE_LAYOUT, T.ROLE_FOLDING):
            out.extend(T.outputs(model, role))
    elif frame == "RIG":
        out = T.outputs(model, T.ROLE_FOLDING) or [model]
    return out


def eevee(scene, size):
    """EEVEE settings for crisp folds; `size` is the subject's radius."""
    _set(scene.render, "engine", "BLENDER_EEVEE_NEXT")
    if scene.render.engine not in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        _set(scene.render, "engine", "BLENDER_EEVEE")
    e = scene.eevee
    _set(e, "taa_samples", 16)
    _set(e, "use_taa_reprojection", True)
    # Screen-space AO in the creases; paper is matt, so nothing is traced.
    _set(e, "use_raytracing", True)
    _set(e, "ray_tracing_method", "PROBE")
    r = getattr(e, "ray_tracing_options", None)
    if r is not None:
        _set(r, "trace_max_roughness", 0.0)
        _set(r, "resolution_scale", "2")
        _set(r, "use_denoise", True)
        _set(r, "denoise_bilateral", False)
    _set(e, "use_fast_gi", True)
    _set(e, "fast_gi_method", "AMBIENT_OCCLUSION_ONLY")
    _set(e, "fast_gi_resolution", "2")
    _set(e, "fast_gi_step_count", 4)
    _set(e, "fast_gi_ray_count", 2)
    _set(e, "fast_gi_quality", 0.25)
    _set(e, "fast_gi_distance", 0.25 * size)  # creases and corners, not the whole model
    _set(e, "fast_gi_thickness_near", 0.02 * size)
    _set(e, "fast_gi_bias", 0.05)
    _set(e, "use_shadows", True)
    _set(e, "shadow_ray_count", 1)
    _set(e, "shadow_step_count", 4)
    _set(e, "shadow_pool_size", "256")
    _set(scene.view_settings, "view_transform", "Standard")
    _set(scene.view_settings, "look", "None")


def sun(scene, strength=0.6):
    ob = bpy.data.objects.get(SUN)
    if ob is None or ob.type != "LIGHT":
        ob = bpy.data.objects.new(SUN, bpy.data.lights.new(SUN, "SUN"))
    ob.data.energy = strength
    ob.data.angle = math.radians(3.0)
    ob.data.use_shadow = True
    # From above, in front and to the left, as from a window.
    ob.rotation_euler = (math.radians(50.0), math.radians(-20.0), math.radians(-35.0))
    if scene.collection.objects.get(ob.name) is None:
        scene.collection.objects.link(ob)
    return ob


def _corners(objs):
    pts = []
    for o in objs:
        if o is not None and o.type == "MESH":
            pts.extend(o.matrix_world @ Vector(c) for c in o.bound_box)
    return pts


def camera(scene, objs, make_active=True, margin=0.88):
    """A camera in front of the subjects, a little above and to the side
    (a three-quarter view), close enough that they fill the frame."""
    centre, radius = _bounds(objs)
    ob = bpy.data.objects.get(CAMERA)
    if ob is None or ob.type != "CAMERA":
        ob = bpy.data.objects.new(CAMERA, bpy.data.cameras.new(CAMERA))
    cam = ob.data
    cam.lens = 50.0
    cam.sensor_fit = "AUTO"
    r = scene.render
    aspect = max(r.resolution_x * r.pixel_aspect_x, 1) / max(r.resolution_y * r.pixel_aspect_y, 1)
    half = math.atan(18.0 / cam.lens)  # 36 mm sensor across the longer side
    tan_h = math.tan(half) if aspect >= 1 else math.tan(half) * aspect
    tan_v = math.tan(half) / aspect if aspect >= 1 else math.tan(half)
    aim = Vector((0.45, -1.0, 0.6)).normalized()  # front, right, above
    forward = -aim
    right = forward.cross(Vector((0.0, 0.0, 1.0))).normalized()
    up = right.cross(forward).normalized()
    # Each corner must fit inside the view at its own depth.
    dist = radius
    for p in _corners(objs) or [centre]:
        v = p - centre
        x, y, z = v.dot(right), v.dot(up), v.dot(forward)
        dist = max(dist, abs(x) / (tan_h * margin) - z, abs(y) / (tan_v * margin) - z)
    ob.location = centre + aim * dist
    ob.rotation_euler = forward.to_track_quat("-Z", "Y").to_euler()
    cam.clip_start = max(dist * 0.01, 1e-4)
    cam.clip_end = (dist + radius) * 20.0
    if scene.collection.objects.get(ob.name) is None:
        scene.collection.objects.link(ob)
    if make_active:
        scene.camera = ob
    return ob


def look_through(context):
    win = context.window
    n = 0
    if win is None:
        return n
    for area in win.screen.areas:
        if area.type == "VIEW_3D" and area.spaces.active.region_3d is not None:
            area.spaces.active.region_3d.view_perspective = "CAMERA"
            n += 1
    return n


def _sunrise_path(name):
    p = bpy.utils.system_resource("DATAFILES", path=f"studiolights/world/{name}")
    if not p:
        p = os.path.join(bpy.utils.resource_path("LOCAL"), "datafiles", "studiolights",
                         "world", name)
    return p if p and os.path.exists(p) else None


def _seen_grey(w, light_socket):
    """Light with one thing, show the camera a soft grey."""
    nt = w.node_tree
    out = next((n for n in nt.nodes if n.type == "OUTPUT_WORLD"), None)
    if out is None:
        return
    seen = nt.nodes.new("ShaderNodeBackground")
    seen.inputs["Color"].default_value = (0.32, 0.33, 0.35, 1.0)
    lp = nt.nodes.new("ShaderNodeLightPath")
    mix = nt.nodes.new("ShaderNodeMixShader")
    nt.links.new(lp.outputs["Is Camera Ray"], mix.inputs[0])
    nt.links.new(light_socket, mix.inputs[1])
    nt.links.new(seen.outputs[0], mix.inputs[2])
    nt.links.new(mix.outputs[0], out.inputs["Surface"])
    lp.location, seen.location, mix.location = (-500, 300), (-300, -200), (100, 0)


def _world(name, colour=(1.0, 1.0, 1.0), strength=1.0, hdri=None):
    w = bpy.data.worlds.get(name)
    if w is not None:
        return w
    w = bpy.data.worlds.new(name)
    w.use_nodes = True
    nt = w.node_tree
    bg = next((n for n in nt.nodes if n.type == "BACKGROUND"), None)
    if bg is None:
        return w
    bg.inputs["Color"].default_value = (*colour, 1.0)
    bg.inputs["Strength"].default_value = strength
    path = _sunrise_path(hdri) if hdri else None
    if path:
        env = nt.nodes.new("ShaderNodeTexEnvironment")
        env.image = bpy.data.images.load(path, check_existing=True)
        env.location = (-500, 0)
        nt.links.new(env.outputs["Color"], bg.inputs["Color"])
    _seen_grey(w, bg.outputs[0])
    w.color = colour
    return w


def _key(scene, sun_ob, on):
    """Renders-only sun, stronger than the viewport's, same direction."""
    ob = bpy.data.objects.get(KEY)
    if not on:
        if ob is not None:
            ob.hide_render = True
        return
    if ob is None or ob.type != "LIGHT":
        ob = bpy.data.objects.new(KEY, bpy.data.lights.new(KEY, "SUN"))
    ob.data.energy = 3.0
    ob.data.angle = math.radians(3.0)
    ob.data.use_shadow = True
    ob.rotation_euler = (sun_ob.rotation_euler.copy() if sun_ob is not None else
                         (math.radians(50.0), math.radians(-20.0), math.radians(-35.0)))
    ob.hide_viewport = True
    ob.hide_render = False
    if scene.collection.objects.get(ob.name) is None:
        scene.collection.objects.link(ob)


def render_look(scene, look="CLEAN"):
    """How finished renders are lit (the viewport keeps Material Preview)."""
    sun_ob = bpy.data.objects.get(SUN)
    if look == "OWN":
        _key(scene, None, False)
        if sun_ob is not None:
            sun_ob.hide_render = True
        if scene.world is not None and scene.world.name in OURS:
            before = scene.get("papercraft_world_before")
            scene.world = bpy.data.worlds.get(before) if before else None
        scene["papercraft_render_look"] = look
        return
    if scene.world is not None and scene.world.name not in OURS:
        scene["papercraft_world_before"] = scene.world.name
    if sun_ob is not None:
        sun_ob.hide_render = look != "DAWN"
    _key(scene, sun_ob, look in ("SHOWCASE", "STUDIO"))
    scene.world = {
        "CLEAN": lambda: _world(WHITE),
        "SHOWCASE": lambda: _world(SHOW, (0.85, 0.9, 1.0), 0.5),
        "DAWN": lambda: _world(DAWN, (0.8, 0.75, 0.7), 1.0, "sunrise.exr"),
        "STUDIO": lambda: _world(STUDIO, (0.8, 0.8, 0.8), 1.2, "studio.exr"),
    }[look]()
    if look != "CLEAN":
        _set(scene.eevee, "taa_render_samples", max(64, scene.eevee.taa_render_samples))
    scene["papercraft_render_look"] = look


def _grain_group():
    grp = bpy.data.node_groups.new(GRAIN, "ShaderNodeTree")
    grp.interface.new_socket("Strength", in_out="INPUT", socket_type="NodeSocketFloat")
    grp.interface.new_socket("Fibres per Unit", in_out="INPUT", socket_type="NodeSocketFloat")
    grp.interface.new_socket("Depth", in_out="INPUT", socket_type="NodeSocketFloat")
    grp.interface.new_socket("Normal", in_out="OUTPUT", socket_type="NodeSocketVector")
    n = grp.nodes
    gi, go = n.new("NodeGroupInput"), n.new("NodeGroupOutput")
    tc = n.new("ShaderNodeTexCoord")
    noise = n.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 400.0
    noise.inputs["Detail"].default_value = 6.0
    noise.inputs["Roughness"].default_value = 0.6
    bump = n.new("ShaderNodeBump")
    bump.inputs["Distance"].default_value = 0.0005
    for i, node in enumerate((gi, tc, noise, bump, go)):
        node.location = (i * 220.0 - 440.0, 0.0)
    grp.links.new(tc.outputs["Object"], noise.inputs["Vector"])
    grp.links.new(gi.outputs["Fibres per Unit"], noise.inputs["Scale"])
    grp.links.new(gi.outputs["Depth"], bump.inputs["Distance"])
    grp.links.new(noise.outputs["Fac"], bump.inputs["Height"])
    grp.links.new(gi.outputs["Strength"], bump.inputs["Strength"])
    grp.links.new(bump.outputs["Normal"], go.inputs["Normal"])
    return grp


def paper_texture(model, on=True, strength=0.12, mm_per_unit=None):
    """A fine paper grain on the model's materials: a bump only, so the
    colours you paint and print are untouched. Added only where a material's
    Principled BSDF has nothing on its Normal input (your own bumps are left
    alone); turning it off removes it again. Returns how many materials
    carry it. Edit the 'Papercraft Paper Texture' group to use your own."""
    grp = bpy.data.node_groups.get(GRAIN) or (_grain_group() if on else None)
    count = 0
    for slot in model.material_slots:
        mat = slot.material
        if mat is None or mat.node_tree is None:
            continue
        nt = mat.node_tree
        node = nt.nodes.get(GRAIN)
        if not on:
            if node is not None:
                nt.nodes.remove(node)
            continue
        bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf is None:
            continue
        normal = bsdf.inputs["Normal"]
        if normal.is_linked and (node is None or normal.links[0].from_node != node):
            continue  # the material's own bump: leave it
        if node is None:
            node = nt.nodes.new("ShaderNodeGroup")
            node.name = node.label = GRAIN
            node.node_tree = grp
            node.location = bsdf.location + Vector((-250.0, -420.0))
        node.inputs["Strength"].default_value = strength
        # Fibres about a quarter millimetre on the printed sheet, a hundredth deep.
        mpu = mm_per_unit or 50.0
        node.inputs["Fibres per Unit"].default_value = mpu / 0.25
        node.inputs["Depth"].default_value = 0.01 / mpu
        if not normal.is_linked:
            nt.links.new(node.outputs["Normal"], normal)
        count += 1
    return count


def viewports(context, fold_lines=True):
    """Material Preview with the dawn HDRI and the scene's sun; fold lines."""
    win = context.window
    n = 0
    if win is None:
        return n
    for area in win.screen.areas:
        if area.type != "VIEW_3D":
            continue
        sp = area.spaces.active
        sh = sp.shading
        sh.type = "MATERIAL"
        _set(sh, "studio_light", "sunrise.exr")
        _set(sh, "use_scene_lights", True)
        _set(sh, "use_scene_world", False)
        _set(sh, "studiolight_intensity", 1.0)
        ov = sp.overlay
        _set(ov, "show_wireframes", fold_lines)
        _set(ov, "wireframe_threshold", 0.05)  # only edges where faces meet at an angle
        _set(ov, "wireframe_opacity", 0.35)
        n += 1
    return n


def show_in_renders(model, layout=None, rig=None, source=None):
    """Which papercraft objects appear in renders (None = leave as is)."""
    for role, on in ((T.ROLE_LAYOUT, layout), (T.ROLE_FOLDING, rig)):
        if on is None:
            continue
        for o in T.outputs(model, role):
            o.hide_render = not on
    if source is not None:
        model.hide_render = not source


class PAPERCRAFT_OT_setup_scene(bpy.types.Operator):
    """Viewport and EEVEE settings that suit papercraft: Material Preview lit by the
    dawn HDRI, a sun for crisp shadows, fold lines, ambient occlusion in the creases,
    a camera framing the model and a render look. Light enough for modest graphics
    cards"""

    bl_idname = "papercraft.setup_scene"
    bl_label = "Set Up Scene"
    bl_options = {"REGISTER", "UNDO"}

    look: bpy.props.EnumProperty(name="Render Look", items=LOOKS, default="SHOWCASE")
    sun: bpy.props.BoolProperty(
        name="Sun (shadows)", default=True,
        description="A sun from above left: shadows show at once which way each fold goes")
    sun_strength: bpy.props.FloatProperty(name="Sun Strength", default=0.6, min=0.0, soft_max=3.0)
    fold_lines: bpy.props.BoolProperty(
        name="Fold Lines", default=True,
        description="Faint lines where faces meet at an angle (the Wireframe overlay)")
    grain: bpy.props.BoolProperty(
        name="Paper Texture", default=False,
        description="A fine paper grain on the model's materials (a bump only; colours and "
        "prints untouched). Skips materials with their own bump")
    frame: bpy.props.EnumProperty(
        name="Camera Frames",
        items=[("MODEL", "Model", "Frame the model"),
               ("RIG", "Fold Rig", "Frame the folding sheet"),
               ("ALL", "Everything", "Frame the model, its layout and its rig")],
        default="MODEL")
    cam: bpy.props.BoolProperty(name="Camera", default=True)
    view_camera: bpy.props.BoolProperty(name="Look Through Camera", default=False)

    @classmethod
    def poll(cls, context):
        return T.target(context) is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        col = layout.column()
        col.prop(self, "look")
        col.prop(self, "sun")
        sub = col.column()
        sub.active = self.sun
        sub.prop(self, "sun_strength")
        col.prop(self, "fold_lines")
        col.prop(self, "grain")
        col.separator()
        col.prop(self, "cam")
        sub = col.column()
        sub.active = self.cam
        sub.prop(self, "frame")
        sub.prop(self, "view_camera")

    def execute(self, context):
        scene = context.scene
        model = T.target(context)
        objs = subjects(model, self.frame)
        _, radius = _bounds(objs)
        eevee(scene, radius)
        if self.sun:
            sun(scene, self.sun_strength)
        else:
            ob = bpy.data.objects.get(SUN)
            if ob is not None and scene.collection.objects.get(ob.name) is not None:
                scene.collection.objects.unlink(ob)
        from . import live

        entry = live.cached(model)
        mpu = entry.lay.scale if entry is not None and entry.lay.scale > 0 else None
        grained = paper_texture(model, self.grain, mm_per_unit=mpu)
        render_look(scene, self.look)
        views = viewports(context, self.fold_lines)
        if self.cam:
            camera(scene, objs)
            if self.view_camera:
                look_through(context)
        msg = f"Scene set up: {dict((k, n) for k, n, *_ in LOOKS)[self.look]} look, {views} view(s)"
        if self.grain and not grained:
            msg += "; no paper texture (the materials have no free Normal input)"
        self.report({"INFO"}, msg)
        return {"FINISHED"}


class PAPERCRAFT_OT_render_look(bpy.types.Operator):
    """How finished renders are lit (the viewport, painting, baking and printing
    are not affected)"""

    bl_idname = "papercraft.render_look"
    bl_label = "Render Look"
    bl_options = {"REGISTER", "UNDO"}

    look: bpy.props.EnumProperty(name="Render Look", items=LOOKS, default="SHOWCASE")

    def execute(self, context):
        render_look(context.scene, self.look)
        return {"FINISHED"}


class PAPERCRAFT_OT_frame_camera(bpy.types.Operator):
    """Point the papercraft camera at the model again (after moving or resizing it)"""

    bl_idname = "papercraft.frame_camera"
    bl_label = "Frame Camera"
    bl_options = {"REGISTER", "UNDO"}

    frame: bpy.props.EnumProperty(
        name="Frame",
        items=[("MODEL", "Model", ""), ("RIG", "Fold Rig", ""), ("ALL", "Everything", "")],
        default="MODEL")

    @classmethod
    def poll(cls, context):
        return T.target(context) is not None

    def execute(self, context):
        camera(context.scene, subjects(T.target(context), self.frame))
        return {"FINISHED"}


classes = (PAPERCRAFT_OT_setup_scene, PAPERCRAFT_OT_render_look, PAPERCRAFT_OT_frame_camera)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
