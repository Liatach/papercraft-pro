"""v0.11: catch a Cycles that doesn't decode sRGB textures (pale bakes and
renders while EEVEE looks right) and decode the paint for the bake."""
import base64
import os
import sys
from collections import Counter

import bpy
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import papercraft_pro as addon  # noqa: E402
from papercraft_pro import bake, live  # noqa: E402
from papercraft_pro.ops import svg_documents  # noqa: E402

OUT = os.path.join(ROOT, "tests", "out")
os.makedirs(OUT, exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
addon.register()
ctx = bpy.context

# The check itself: a working Cycles decodes; a texture tagged as raw data
# behaves like a Cycles that skips the decode.
assert bake.textures_decode(ctx) is True
assert bake.textures_decode(ctx, check_colorspace="Non-Color") is False
print("decode check: working Cycles passes, an undecoded texture is caught")

# Paint layer inside a node group (like Paint System).
bpy.ops.mesh.primitive_cube_add()
cube = ctx.active_object
want = (216, 128, 40)
img = bpy.data.images.new("paint", 32, 32)
img.pixels.foreach_set([want[0] / 255, want[1] / 255, want[2] / 255, 1.0] * 1024)
img.pack()
grp = bpy.data.node_groups.new("Paint Layer", "ShaderNodeTree")
grp.interface.new_socket("Color", in_out="OUTPUT", socket_type="NodeSocketColor")
gout = grp.nodes.new("NodeGroupOutput")
gtex = grp.nodes.new("ShaderNodeTexImage")
gtex.image = img
grp.links.new(gtex.outputs["Color"], gout.inputs["Color"])
mat = bpy.data.materials.new("painted")
mat.use_nodes = True
gnode = mat.node_tree.nodes.new("ShaderNodeGroup")
gnode.node_tree = grp
emit = mat.node_tree.nodes.new("ShaderNodeEmission")
mat.node_tree.links.new(gnode.outputs["Color"], emit.inputs["Color"])
mat.node_tree.links.new(emit.outputs[0], mat.node_tree.nodes["Material Output"].inputs["Surface"])
cube.data.materials.append(mat)
ctx.view_layer.objects.active = cube
cube.papercraft.bake_dpi = 80
assert bpy.ops.papercraft.unfold() == {"FINISHED"}
entry = live.cached(cube)


def baked_colour():
    doc = svg_documents(ctx, cube, entry)[0]
    uri = doc.split('href="data:image/png;base64,')[1].split('"')[0]
    p = os.path.join(OUT, "v11_page.png")
    with open(p, "wb") as fh:
        fh.write(base64.b64decode(uri))
    im = bpy.data.images.load(p, check_existing=False)
    w, h = im.size
    a = np.empty(w * h * 4, np.float32)
    im.pixels.foreach_get(a)
    a = a.reshape(-1, 4)
    top = Counter(tuple(int(round(v * 255)) for v in c[:3]) for c in a[a[:, 3] > 0.99]).most_common(1)
    bpy.data.images.remove(im)
    return top[0][0]


before = (len(bpy.data.images), len(bpy.data.node_groups), len(bpy.data.materials))
normal = baked_colour()
print("working Cycles:", normal, "paint", want, "fixed:", bake.bake_pages.textures_fixed)
assert not bake.bake_pages.textures_fixed
assert max(abs(normal[k] - want[k]) for k in range(3)) <= 4

# Force the fix path (as on a machine whose Cycles skips the decode): the
# decoded copies must print the same, true colour.
real = bake.textures_decode
bake.textures_decode = lambda context, check_colorspace="sRGB": False
try:
    fixed = baked_colour()
finally:
    bake.textures_decode = real
print("decoded for the bake:", fixed, "fixed:", bake.bake_pages.textures_fixed)
assert bake.bake_pages.textures_fixed
assert max(abs(fixed[k] - want[k]) for k in range(3)) <= 4
after = (len(bpy.data.images), len(bpy.data.node_groups), len(bpy.data.materials))
assert after == before, f"temporary copies left behind {before} -> {after}"
assert gnode.node_tree == grp and gtex.image == img, "the user's material changed"
print("originals untouched, temporary copies removed")

addon.unregister()
print("V11 OK")
