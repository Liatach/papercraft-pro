"""v0.3: several models, rig lifecycle, layout moves, baked printing."""
import base64
import os
import sys
import xml.dom.minidom

import bpy
from mathutils import Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import papercraft_pro as addon  # noqa: E402
from papercraft_pro import build, editor, live  # noqa: E402
from papercraft_pro import targets as T  # noqa: E402
from papercraft_pro.mesh_io import material_image  # noqa: E402
from papercraft_pro.ops import svg_documents  # noqa: E402

OUT = os.path.join(ROOT, "tests", "out")
os.makedirs(OUT, exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
addon.register()
scene = bpy.context.scene
ctx = bpy.context


def activate(obj):
    if ctx.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for o in ctx.selected_objects:
        o.select_set(False)
    ctx.view_layer.objects.active = obj
    obj.select_set(True)


def unfold(obj, **settings):
    activate(obj)
    for k, v in settings.items():
        setattr(obj.papercraft, k, v)
    assert bpy.ops.papercraft.unfold() == {"FINISHED"}
    return live.cached(obj)


def evaluate():
    scene.frame_set(scene.frame_current)


def assembled_error(obj, entry):
    arm = T.output(obj, T.ROLE_RIG)
    folding = T.output(obj, T.ROLE_FOLDING)
    arm["fold"], arm["assemble"] = 1.0, 1.0
    arm.update_tag()
    ad = arm.animation_data
    if ad is not None and ad.action is not None:  # keyframes win: go to the end
        scene.frame_set(int(ad.action.frame_range[1]))
    else:
        evaluate()
    ev = folding.evaluated_get(ctx.evaluated_depsgraph_get())
    me = ev.to_mesh()
    pos = [ev.matrix_world @ v.co for v in me.vertices]
    ev.to_mesh_clear()
    idx, err = 0, 0.0
    for isl in entry.res.islands:
        for f in isl.order:
            for v in entry.res.faces[f].loop:
                err = max(err, (pos[idx] - Vector(entry.src.verts[v])).length)
                idx += 1
    arm["fold"], arm["assemble"] = 0.0, 0.0
    return err


# ------------------------------------------------------------ 1. two models
bpy.ops.mesh.primitive_cube_add(location=(0, 0, 1))
cube = ctx.active_object
bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, location=(0, 12, 1))
ico = ctx.active_object
e_cube = unfold(cube, pages=1)
e_ico = unfold(ico, pages=1, paper="A5")
lay_cube, lay_ico = T.output(cube, T.ROLE_LAYOUT), T.output(ico, T.ROLE_LAYOUT)
assert lay_cube is not None and lay_ico is not None and lay_cube != lay_ico
assert len(lay_cube.data.polygons) != len(lay_ico.data.polygons)
assert ico.papercraft.page_size() != cube.papercraft.page_size()
# Selecting a layout targets its model.
activate(lay_ico)
assert T.target(ctx) == ico
print("two models: layouts", lay_cube.name, "|", lay_ico.name, "| A4 vs A5 ok")

# ------------------------------------------------------------ 2. rig lifecycle
activate(cube)
assert bpy.ops.papercraft.build_rig() == {"FINISHED"}
assert bpy.ops.papercraft.animate("EXEC_DEFAULT", start=1, fold_frames=20, assemble_frames=10) == {"FINISHED"}
arm = T.output(cube, T.ROLE_RIG)
action = arm.animation_data.action
assert not build.rig_is_stale(cube, live.cached(cube))
# Change a cut -> stale.
p = editor.pick(cube, live.cached(cube), Vector((0.95, 0, 5)), Vector((0, 0, -1)))
editor.apply_pick(cube, p)
entry = live.regenerate(scene, ctx.evaluated_depsgraph_get(), cube, force=True)
assert build.rig_is_stale(cube, entry)
# Rebuild from Pose Mode on the old rig (the reported trouble spot).
activate(arm)
bpy.ops.object.mode_set(mode="POSE")
assert bpy.ops.papercraft.build_rig() == {"FINISHED"}, "rebuild from pose mode"
arm2 = T.output(cube, T.ROLE_RIG)
assert ctx.mode == "OBJECT", ctx.mode
assert arm2.animation_data is not None and arm2.animation_data.action == action, "keyframes lost"
assert not build.rig_is_stale(cube, entry)
err = assembled_error(cube, entry)
print(f"rebuild from pose mode ok, keyframes kept, assembled error {err:.2e}")
assert err < 1e-4
# Rigs of the two models are independent.
activate(ico)
bpy.ops.papercraft.build_rig()
assert T.output(cube, T.ROLE_RIG) is not None and T.output(ico, T.ROLE_RIG) is not None
# Remove + rebuild.
activate(cube)
assert bpy.ops.papercraft.remove_rig() == {"FINISHED"}
assert T.output(cube, T.ROLE_RIG) is None and T.output(cube, T.ROLE_FOLDING) is None
assert T.output(ico, T.ROLE_RIG) is not None, "removing one rig touched the other"
print("remove rig ok, other model untouched")

# ------------------------------------------------------------ 2b. orientation
activate(ico)
entry_i = live.cached(ico)
arm_i = T.output(ico, T.ROLE_RIG)
fold_i = T.output(ico, T.ROLE_FOLDING)


def rig_positions(fold, assemble):
    arm_i["fold"], arm_i["assemble"] = fold, assemble
    arm_i.update_tag()
    evaluate()
    ev = fold_i.evaluated_get(ctx.evaluated_depsgraph_get())
    me = ev.to_mesh()
    pos = [ev.matrix_world @ v.co for v in me.vertices]
    normals = [ev.matrix_world.to_3x3() @ p.normal for p in me.polygons]
    ev.to_mesh_clear()
    return pos, normals


pos, normals = rig_positions(0.0, 0.0)
nfaces = sum(len(i.order) for i in entry_i.res.islands)
up = sum(1 for n in normals[:nfaces] if n.z > 0.99)
print(f"flat sheet: {up}/{nfaces} pieces printed side up")
assert up == nfaces
table = min(p.z for p in pos)
pos, _ = rig_positions(1.0, 0.0)
idx = 0
for isl in entry_i.res.islands:
    offs = []
    for f in isl.order:
        for v in entry_i.res.faces[f].loop:
            offs.append(pos[idx] - Vector(entry_i.src.verts[v]))
            idx += 1
    spread = max((o - offs[0]).length for o in offs)
    assert spread < 1e-4, f"folded island is not just a translated model ({spread})"
low = min(p.z for p in pos[:idx])
print(f"folded beside layout: upright (translation only), resting at table z {low - table:+.1e}")
assert abs(low - table) < 1e-4
arm_i["fold"], arm_i["assemble"] = 0.0, 0.0

# ------------------------------------------------------------ 3. move layout
lay_cube.location += Vector((3, -2, 0.5))
loc = lay_cube.location.copy()
entry = live.regenerate(scene, ctx.evaluated_depsgraph_get(), cube, force=True)
assert (T.output(cube, T.ROLE_LAYOUT).location - loc).length < 1e-6, "layout jumped back"
activate(cube)
bpy.ops.papercraft.build_rig()
err = assembled_error(cube, live.cached(cube))
print(f"moved layout kept, rig follows it, assembled error {err:.2e}")
assert err < 1e-4

# ------------------------------------------------------------ 4. bake
bpy.ops.mesh.primitive_monkey_add(location=(-6, 0, 1))
monkey = ctx.active_object
activate(monkey)
# Paint System style: own UV map from Smart UV Project, set active.
uv = monkey.data.uv_layers.new(name="PS_UVMap")
monkey.data.uv_layers.active = uv
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.uv.smart_project()
bpy.ops.object.mode_set(mode="OBJECT")
# Vertex colour layer (blue-ish nose side).
col = monkey.data.color_attributes.new("Col", "BYTE_COLOR", "CORNER")
for p in monkey.data.polygons:
    for li in p.loop_indices:
        col.data[li].color = (1, 1, 1, 1) if p.normal.y < 0 else (0.4, 0.6, 1.0, 1)
img = bpy.data.images.new("paint", 512, 512)
img.generated_type = "COLOR_GRID"
group = bpy.data.node_groups.new("PS Layers", "ShaderNodeTree")
group.interface.new_socket("Color", in_out="OUTPUT", socket_type="NodeSocketColor")
gn = group.nodes
out = gn.new("NodeGroupOutput")
uvn = gn.new("ShaderNodeUVMap")
uvn.uv_map = "PS_UVMap"
tex = gn.new("ShaderNodeTexImage")
tex.image = img
vc = gn.new("ShaderNodeVertexColor")
vc.layer_name = "Col"
mix = gn.new("ShaderNodeMix")
mix.data_type = "RGBA"
mix.blend_type = "MULTIPLY"
mix.inputs["Factor"].default_value = 1.0
group.links.new(uvn.outputs["UV"], tex.inputs["Vector"])
group.links.new(tex.outputs["Color"], mix.inputs["A"])
group.links.new(vc.outputs["Color"], mix.inputs["B"])
group.links.new(mix.outputs["Result"], out.inputs["Color"])
mat = bpy.data.materials.new("PS material")
mat.use_nodes = True
gnode = mat.node_tree.nodes.new("ShaderNodeGroup")
gnode.node_tree = group
mat.node_tree.links.new(gnode.outputs["Color"], mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"])
monkey.data.materials.append(mat)

found = material_image(mat)
print("image inside node group:", found[0].name if found[0] else None, "uv:", found[1])
assert found[0] == img and found[1] == "PS_UVMap"

entry = unfold(monkey, pages=2, bake_dpi=100, texture_mode="BAKE")
lay = T.output(monkey, T.ROLE_LAYOUT)
assert "PS_UVMap" in lay.data.uv_layers and "UVMap" in lay.data.uv_layers, "UV maps not copied"
assert "Col" in lay.data.color_attributes, "colour layer not copied"
docs = svg_documents(ctx, monkey, entry)
for n, d in enumerate(docs, 1):
    xml.dom.minidom.parseString(d)
    with open(f"{OUT}/baked_monkey_p{n}.svg", "w") as fh:
        fh.write(d)
assert "clip-path=\"url(#pieces)\"" in docs[0]
# Decode the baked page and check it has varied, opaque colour where pieces are.
uri = docs[0].split('href="data:image/png;base64,')[1].split('"')[0]
png = base64.b64decode(uri)
with open(f"{OUT}/baked_page1.png", "wb") as fh:
    fh.write(png)
print("baked page 1 png kB:", len(png) // 1024)
monkey.papercraft.tab_texture = True
docs = svg_documents(ctx, monkey, entry)
with open(f"{OUT}/baked_monkey_tabs_p1.svg", "w") as fh:
    fh.write(docs[0])
# Image mode now finds the grouped image and uses PS_UVMap.
monkey.papercraft.texture_mode = "IMAGE"
docs = svg_documents(ctx, monkey, entry)
assert "<pattern" in docs[0]
with open(f"{OUT}/image_mode_monkey_p1.svg", "w") as fh:
    fh.write(docs[0])
assert ctx.mode == "OBJECT"
print("bake + image modes ok; selection restored:", ctx.view_layer.objects.active.name)

# ------------------------------------------------------------ 5. undo safety
monkey.papercraft.texture_mode = "COLOR"
bpy.ops.ed.undo_push(message="before")
monkey.data.materials[0].name = "renamed"
bpy.ops.ed.undo_push(message="after")
monkey_name = monkey.name
bpy.ops.ed.undo()
monkey = bpy.data.objects[monkey_name]  # undo replaces every datablock
activate(monkey)
entry = live.get_or_build(ctx, monkey)
docs = svg_documents(ctx, monkey, entry)
print("export after undo ok:", len(docs), "page(s)")
bpy.ops.ed.undo_push(message="touch")
bpy.ops.ed.undo()
monkey = bpy.data.objects[monkey_name]
activate(monkey)
pc = monkey.papercraft
pc.texture_mode, pc.bake_dpi, pc.number_mode = "BAKE", 60, "CLEAN"
pc.line_style, pc.line_color, pc.line_opacity = "CUSTOM", (1, 1, 1), 0.6
docs = svg_documents(ctx, monkey, live.get_or_build(ctx, monkey))
with open(f"{OUT}/after_undo_bake_p1.svg", "w") as fh:
    fh.write(docs[0])
print("bake after undo ok")

# ------------------------------------------------------------ 6. materials untouched
# Two Material Outputs, the inactive one selected (the donut file's setup):
# baking must not switch the active output or leave nodes behind.
bpy.ops.mesh.primitive_cube_add(location=(20, 0, 0))
box = ctx.active_object
mat2 = bpy.data.materials.new("two outputs")
mat2.use_nodes = True
nt = mat2.node_tree
spare = nt.nodes["Material Output"]
emit = nt.nodes.new("ShaderNodeEmission")
emit.inputs["Color"].default_value = (0.9, 0.2, 0.1, 1)
live_out = nt.nodes.new("ShaderNodeOutputMaterial")
nt.links.new(emit.outputs[0], live_out.inputs["Surface"])
live_out.is_active_output = True
nt.nodes.active = spare
spare.is_active_output = False
live_out.is_active_output = True
box.data.materials.append(mat2)
before = [(n.name, n.is_active_output) for n in nt.nodes if n.type == "OUTPUT_MATERIAL"]
count = len(nt.nodes)
box.papercraft.bake_dpi = 60
entry = unfold(box, texture_mode="BAKE", bake_pass="AUTO")
svg_documents(ctx, box, entry)
from papercraft_pro import bake as bake_mod  # noqa: E402
after = [(n.name, n.is_active_output) for n in nt.nodes if n.type == "OUTPUT_MATERIAL"]
print("outputs before/after:", before, after, "| auto pass:", bake_mod.bake_pages.last_pass)
assert before == after and len(nt.nodes) == count, "bake modified the user's material"
assert bake_mod.bake_pages.last_pass == "EMIT"
assert not [m for m in bpy.data.materials if m.name.startswith("Papercraft Bake")]

addon.unregister()
print("V03 OK")
