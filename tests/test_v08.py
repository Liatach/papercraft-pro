"""v0.8: Art + Cut Lines export, tab art, Grey Tabs off, bake colour
self-check, one-at-a-time fold sequence."""
import base64
import os
import sys
from collections import Counter

import bpy
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import papercraft_pro as addon  # noqa: E402
from papercraft_pro import bake, build, live  # noqa: E402
from papercraft_pro import targets as T  # noqa: E402
from papercraft_pro.ops import svg_documents, write_pages  # noqa: E402
from papercraft_pro.textures import _encode_pixels  # noqa: E402

OUT = os.path.join(ROOT, "tests", "out")
os.makedirs(OUT, exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
addon.register()
ctx = bpy.context
scene = ctx.scene


def layer(doc, name):
    return doc.split(f'<g id="{name}"')[1].split("</g>")[0] if f'<g id="{name}"' in doc else None


# A cube with a quadrant texture of known sRGB colours.
bpy.ops.mesh.primitive_cube_add()
cube = ctx.active_object
S = 64
want = [(200, 30, 30), (30, 160, 60), (40, 60, 200), (240, 200, 40)]
img = bpy.data.images.new("q", S, S)
px = np.zeros((S, S, 4), np.float32)
px[..., 3] = 1
for i, c in enumerate(want):
    y, x = divmod(i, 2)
    px[y * 32:(y + 1) * 32, x * 32:(x + 1) * 32, :3] = np.array(c) / 255
img.pixels.foreach_set(px.ravel())
img.pack()
mat = bpy.data.materials.new("m")
mat.use_nodes = True
tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
tex.image = img
tex.interpolation = "Closest"
mat.node_tree.links.new(tex.outputs[0], mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"])
cube.data.materials.append(mat)
ctx.view_layer.objects.active = cube
cube.papercraft.bake_dpi = 100
assert bpy.ops.papercraft.unfold() == {"FINISHED"}
entry = live.cached(cube)
props = cube.papercraft
ntabs = len(entry.lay.tabs)

# ------------------------------------------------------------ Art + Cut Lines
props.texture_mode = "IMAGE"
props.export_content = "PRINT"
plain = svg_documents(ctx, cube, entry)[0]
props.export_content = "ART_CUT"
art = svg_documents(ctx, cube, entry)[0]
assert layer(art, "mountain") is None and layer(art, "valley") is None
assert layer(art, "cut").count("<path") == layer(plain, "cut").count("<path")
extra = layer(art, "faces").count("<polygon") - layer(plain, "faces").count("<polygon")
print(f"Art + Cut (image textures): no fold lines; {extra} extra art polygons for {ntabs} tabs")
assert extra >= ntabs
files = write_pages(ctx, cube, entry, os.path.join(OUT, "v08_export.svg"))
assert os.path.isfile(os.path.join(OUT, "v08_export_art.svg"))

# Tab art continues the face's own texture: sample the tab pattern UVs land
# on the same image (pattern references tex0).
assert art.count('href="#tex0"') > plain.count('href="#tex0"')

# Baked Art + Cut: tabs are part of the clip (art shows on them).
props.texture_mode = "BAKE"
baked = svg_documents(ctx, cube, entry)[0]
clip = baked.split('<clipPath id="pieces"')[1].split("</clipPath>")[0]
assert clip.count("<polygon") >= ntabs + 6
assert layer(baked, "mountain") is None
print("Art + Cut (baked): tabs carry the baked art")

# ------------------------------------------------------------ Grey Tabs off
props.export_content = "PRINT"
props.texture_mode = "WHITE"
props.tab_fill = True
grey = svg_documents(ctx, cube, entry)[0]
props.tab_fill = False
white = svg_documents(ctx, cube, entry)[0]
assert 'id="tabs" inkscape:groupmode="layer" inkscape:label="tabs" style="fill:#' in grey
assert 'inkscape:label="tabs" style="fill:none;stroke:none"' in white
assert layer(white, "cut").count("<path") == layer(grey, "cut").count("<path")
print("Grey Tabs off: tabs unfilled, still outlined by cut lines")
props.tab_fill = True

# ------------------------------------------------------------ colour check
props.export_content = "PRINT"
props.texture_mode = "BAKE"
for bp in ("BASE", "EMIT", "COMBINED"):
    props.bake_pass = bp
    if bp != "BASE":  # emission / render: colour through an emission shader
        em = mat.node_tree.nodes.new("ShaderNodeEmission")
        mat.node_tree.links.new(tex.outputs[0], em.inputs[0])
        out = mat.node_tree.nodes["Material Output"]
        mat.node_tree.links.new(em.outputs[0], out.inputs["Surface"])
    doc = svg_documents(ctx, cube, entry)[0]
    uri = doc.split('href="data:image/png;base64,')[1].split('"')[0]
    p = os.path.join(OUT, f"v08_colour_{bp}.png")
    with open(p, "wb") as fh:
        fh.write(base64.b64decode(uri))
    im = bpy.data.images.load(p)
    w, h = im.size
    a = np.empty(w * h * 4, np.float32)
    im.pixels.foreach_get(a)
    a = a.reshape(-1, 4)
    top = [c for c, _ in Counter(tuple(int(round(v * 255)) for v in c[:3]) for c in a[a[:, 3] > 0.99]).most_common(4)]
    print(f"bake {bp}: check steps {bake.bake_pages.srgb_steps}, colours {sorted(top)}")
    for c in want:
        assert any(max(abs(c[k] - t[k]) for k in range(3)) <= 6 for t in top), (bp, c, top)

# The check corrects whatever storage Blender used.
probe = bpy.data.images.new("probe", 8, 8, alpha=True)
expect = {}
for steps_in, v in ((0, 0.2), (1, 0.4845), (2, 0.7231)):
    probe.pixels.foreach_set([v, v, v, 1.0] * 64)
    expect[steps_in] = bake._srgb_steps(probe)
assert expect == {0: 1, 1: 0, 2: -1}, expect
probe.pixels.foreach_set([0.7231, 0.7231, 0.7231, 1.0] * 64)
png = _encode_pixels(probe, 8, srgb_steps=-1)
probe_path = os.path.join(OUT, "v08_probe.png")
with open(probe_path, "wb") as fh:
    fh.write(png)
q = bpy.data.images.load(probe_path)
assert abs(q.pixels[0] - 0.4845) < 0.01
print("colour check: linear, sRGB and double-sRGB storage all print correctly")
bpy.data.images.remove(probe)

# ------------------------------------------------------------ fold sequence
props.fold_one_by_one = True
props.fold_overlap = 0.0
assert bpy.ops.papercraft.build_rig() == {"FINISHED"}
arm = T.output(cube, T.ROLE_RIG)
assert not build.rig_is_stale(cube, entry)
for fc in arm.animation_data.drivers:
    assert fc.driver.is_simple_expression, fc.driver.expression
isl = entry.res.islands[0]


def angles(t):
    arm["fold"] = t
    arm.update_tag()
    scene.frame_set(scene.frame_current)
    ev = arm.evaluated_get(ctx.evaluated_depsgraph_get())
    return {pb.name: pb.rotation_euler.y for pb in ev.pose.bones}


hinges = [b.name for b in arm.pose.bones if b.name != f"F{isl.root}"]
done_at = {}
steps = 400
for i in range(steps + 1):
    a = angles(i / steps)
    for n in hinges:
        if n not in done_at and abs(a[n]) > 1e-6:
            done_at[n] = i / steps
order = sorted(hinges, key=lambda n: done_at[n])
tabs_first = all(n.startswith("T") for n in order[:ntabs])
print(f"one at a time: {len(order)} hinges start in turn; glue tabs first: {tabs_first}; "
      f"first face {order[ntabs]}, last {order[-1]}")
assert tabs_first
for f in isl.order:  # every face starts after all of its children
    if f == isl.root:
        continue
    parent = isl.parent[f][0]
    if parent != isl.root:
        assert done_at[f"F{f}"] < done_at[f"F{parent}"], (f, parent)
full = angles(1.0)
props.fold_one_by_one = False
assert build.rig_is_stale(cube, entry), "changing the sequence should flag a rebuild"
bpy.ops.papercraft.build_rig()
arm = T.output(cube, T.ROLE_RIG)
together = angles(1.0)
assert all(abs(full[n] - together[n]) < 1e-5 for n in hinges), "end pose must match"
mid = angles(0.5)
assert all(abs(mid[n] - 0.5 * together[n]) < 1e-5 for n in hinges)
print("fold=1 matches the all-together rig; together mode is linear as before")

addon.unregister()
print("V08 OK")
