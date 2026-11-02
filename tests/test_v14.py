"""v0.14: assembly page and piece letters; true colours while painting."""
import os
import re
import sys

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import papercraft_pro as addon  # noqa: E402
from papercraft_pro import colours as C  # noqa: E402
from papercraft_pro import live  # noqa: E402
from papercraft_pro.core import assembly as A  # noqa: E402
from papercraft_pro.ops import svg_documents, write_pages, write_pdf  # noqa: E402

OUT = os.path.join(ROOT, "tests", "out")
os.makedirs(OUT, exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
addon.register()
ctx = bpy.context
scene = ctx.scene

assert [A.letter(n) for n in (0, 1, 25, 26, 27, 51, 52)] == ["A", "B", "Z", "AA", "AB", "AZ", "BA"]

# ------------------------------------------------------------ assembly
bpy.ops.mesh.primitive_monkey_add()
monkey = ctx.active_object
mat = bpy.data.materials.new("skin")
mat.use_nodes = True
monkey.data.materials.append(mat)
ctx.view_layer.objects.active = monkey
props = monkey.papercraft
props.texture_mode = "WHITE"
props.pages = 2
assert bpy.ops.papercraft.unfold() == {"FINISHED"}
entry = live.cached(monkey)
res, lay = entry.res, entry.lay
labels = A.piece_labels(res, lay)
assert sorted(labels) == sorted(i.index for i in res.islands)
assert sorted(labels.values()) == [A.letter(n) for n in range(len(res.islands))]
# Reading order: page by page.
by_letter = sorted(labels, key=lambda i: labels[i])
pages_in_order = [lay.placements[i].page for i in by_letter]
assert pages_in_order == sorted(pages_in_order)
print(f"{len(labels)} pieces lettered A..{A.letter(len(labels) - 1)} in reading order")

docs = svg_documents(ctx, monkey, entry)
assert len(docs) == lay.page_count + 1 and A.MARK in docs[-1]
asm = docs[-1]
for name in ("front", "back", "top", "bottom"):
    assert f'id="view_{name}"' in asm
key = asm.split('<g id="key"')[1]
for i, letter in labels.items():
    assert f">{letter} · page {lay.placements[i].page + 1}<" in key, letter
shown = set(re.findall(r'text-anchor="middle" style="font-family:sans-serif;'
                       r'font-size:[\d.]+px;fill:#111111">([A-Z]+)<', asm))
print(f"assembly page: 4 views, key for all {len(labels)}, {len(shown)} lettered on the views")
assert labels[max(labels, key=lambda i: len(res.islands[i].faces))] in shown

# Piece letters on the sheets, matching.
sheet_letters = []
for d in docs[:-1]:
    body = d.split('<g id="pieces"')[1].split("</g>")[0]
    sheet_letters += re.findall(r">([A-Z]+)</text>", body)
assert sorted(sheet_letters) == sorted(labels.values())
props.number_mode = "OFF"
docs_off = svg_documents(ctx, monkey, entry)
assert all(d.split('<g id="pieces"')[1].split("</g>")[0].count("<text") == 0
           for d in docs_off[:-1])
props.number_mode = "FACES"
props.assembly_page = False
assert len(svg_documents(ctx, monkey, entry)) == lay.page_count
props.assembly_page = True
print("sheets: every piece carries its letter (none with Numbers Off)")

paths = write_pages(ctx, monkey, entry, os.path.join(OUT, "v14_monkey.svg"))
assert os.path.isfile(os.path.join(OUT, "v14_monkey_assembly.svg"))
pdf = write_pdf(ctx, monkey, entry, os.path.join(OUT, "v14_monkey.pdf"))
data = open(pdf, "rb").read()
assert len(re.findall(rb"/Type /Page\b", data)) == lay.page_count + 1
print("files: _assembly.svg beside the sheets; the PDF ends with the assembly page")

# ------------------------------------------------------------ true colours
nt = mat.node_tree
bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
tex = nt.nodes.new("ShaderNodeTexImage")
nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
out = next(n for n in nt.nodes if n.type == "OUTPUT_MATERIAL")
# A layer-stack material: colour from a group straight to the output.
ps = bpy.data.materials.new("layers")
ps.use_nodes = True
pnt = ps.node_tree
pnt.nodes.remove(next(n for n in pnt.nodes if n.type == "BSDF_PRINCIPLED"))
emit = pnt.nodes.new("ShaderNodeEmission")
pnt.links.new(emit.outputs[0], next(n for n in pnt.nodes if n.type == "OUTPUT_MATERIAL").inputs[0])
monkey.data.materials.append(ps)
before_ps = len(pnt.nodes)

scene.view_settings.view_transform = "AgX"
assert C.enter(scene, monkey)
assert scene.view_settings.view_transform == "Standard"
src = out.inputs["Surface"].links[0].from_node
assert src.name == C.MIX
true_emit = nt.nodes[C.EMIT]
assert true_emit.inputs["Color"].links[0].from_node == tex, "shows the painted image unlit"
assert len(pnt.nodes) == before_ps, "layer-stack materials are left alone"
assert not C.enter(scene, monkey), "entering twice does nothing"
assert C.leave(scene)
assert scene.view_settings.view_transform == "AgX"
assert out.inputs["Surface"].links[0].from_node == bsdf
assert not any(nt.nodes.get(k) for k in C.OURS)
print("true colours: Standard view, paint shown unlit, all put back on leaving")

# The mode switch drives it.
C._last["mode"] = "OBJECT"
bpy.ops.object.mode_set(mode="TEXTURE_PAINT")
C._mode_changed()
assert C.KEY in scene and nt.nodes.get(C.EMIT) is not None
bpy.ops.object.mode_set(mode="OBJECT")
C._mode_changed()
assert C.KEY not in scene and nt.nodes.get(C.EMIT) is None
props.true_colours = False
bpy.ops.object.mode_set(mode="TEXTURE_PAINT")
C._mode_changed()
assert C.KEY not in scene, "switched off: nothing changes"
bpy.ops.object.mode_set(mode="OBJECT")
C._mode_changed()
props.true_colours = True
print("Texture Paint on/off switches it; the setting turns it off")

# A crash mid-paint: opening the file tidies it.
C.enter(scene, monkey)
del scene[C.KEY]  # as if the record were lost
assert C.heal() == 1
assert out.inputs["Surface"].links[0].from_node == bsdf and nt.nodes.get(C.EMIT) is None
print("left-over preview from a crash is healed")

# Baking never sees the preview colours as anything but the paint.
addon.unregister()
print("V14 OK")
