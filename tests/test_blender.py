"""End-to-end test inside Blender (bpy module)."""
import math
import os
import sys
import xml.dom.minidom

import bpy
from mathutils import Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import papercraft_unfold as addon  # noqa: E402
from papercraft_unfold import live  # noqa: E402
from papercraft_unfold.core.svg import export_pages  # noqa: E402

OUT = os.path.join(ROOT, "tests", "out")
os.makedirs(OUT, exist_ok=True)

bpy.ops.wm.read_factory_settings(use_empty=True)
addon.register()
scene = bpy.context.scene


def make(kind):
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    if kind == "cube":
        bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 1))
    elif kind == "ico":
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, radius=1, location=(3, 2, 1))
    elif kind == "monkey":
        bpy.ops.mesh.primitive_monkey_add(location=(0, 0, 1), rotation=(0, 0, 0.4))
    elif kind == "cylinder":
        bpy.ops.mesh.primitive_cylinder_add(vertices=16, location=(0, 0, 1), scale=(1, 1, 2))
    obj = bpy.context.active_object
    mat = bpy.data.materials.new(f"{kind}_mat")
    mat.diffuse_color = (0.2, 0.6, 0.9, 1)
    obj.data.materials.append(mat)
    return obj


def check_rig(entry):
    arm = bpy.data.objects["Papercraft Rig"]
    folding = bpy.data.objects["Papercraft Folding"]
    res = entry.res
    errs = {}
    for fold, assemble in ((0.0, 0.0), (1.0, 1.0), (0.5, 0.0)):
        arm["fold"] = fold
        arm["assemble"] = assemble
        arm.update_tag()
        scene.frame_set(scene.frame_current)
        dg = bpy.context.evaluated_depsgraph_get()
        ev = folding.evaluated_get(dg)
        me = ev.to_mesh()
        pos = [ev.matrix_world @ v.co for v in me.vertices]
        ev.to_mesh_clear()
        idx = 0
        err = 0.0
        for isl in res.islands:
            for f in isl.order:
                face = res.faces[f]
                for k, vi in enumerate(face.loop):
                    if (fold, assemble) == (1.0, 1.0):
                        err = max(err, (pos[idx] - Vector(entry.src.verts[vi])).length)
                    elif (fold, assemble) == (0.0, 0.0):
                        err = max(err, abs(pos[idx].z - pos[0].z))  # flat
                    elif (fold, assemble) == (0.5, 0.0):
                        err = max(err, pos[idx].z - pos[0].z)  # rises
                    idx += 1
        errs[(fold, assemble)] = err
    return errs


for kind in ("cube", "ico", "cylinder", "monkey"):
    obj = make(kind)
    props = scene.papercraft
    props.source = obj
    props.pages = 1 if kind != "monkey" else 2
    assert bpy.ops.papercraft.unfold() == {"FINISHED"}
    entry = live.CACHE[scene.name]
    layout = bpy.data.objects["Papercraft Layout"]
    assert len(layout.data.polygons) > 0
    assert bpy.ops.papercraft.build_rig() == {"FINISHED"}
    errs = check_rig(entry)
    size = max(obj.dimensions)
    print(
        f"{kind:8s} pieces={props.info_islands:3d} pages={props.info_pages} fits={props.info_fits} "
        f"size={props.info_size_mm:.0f}mm glue={props.info_glue} "
        f"flat_err={errs[(0.0, 0.0)]:.2e} assembled_err={errs[(1.0, 1.0)] / size:.2e} "
        f"msg={props.info_message!r}"
    )
    assert errs[(0.0, 0.0)] < 1e-5 * size, errs
    assert errs[(1.0, 1.0)] < 5e-3 * size, errs
    assert errs[(0.5, 0.0)] > 0.05 * size, ("half-folded pieces should rise", errs)
    docs = export_pages(entry.res, entry.lay, colors=entry.src.colors, title=kind)
    for n, d in enumerate(docs, 1):
        xml.dom.minidom.parseString(d)
        with open(f"{OUT}/{kind}_p{n}.svg", "w") as fh:
            fh.write(d)

# Keyframes
assert bpy.ops.papercraft.animate("EXEC_DEFAULT", start=1, fold_frames=40, assemble_frames=30) == {
    "FINISHED"
}
arm = bpy.data.objects["Papercraft Rig"]
fcurves = [fc.data_path for fc in arm.animation_data.action.fcurves] if hasattr(
    arm.animation_data.action, "fcurves"
) else ["layered"]
print("keyed:", fcurves, "frame_end", scene.frame_end)

# Edit-mode seams + live path
obj = make("cube")
props.source = obj
props.live = True
bpy.ops.object.mode_set(mode="EDIT")
import bmesh  # noqa: E402

bm = bmesh.from_edit_mesh(obj.data)
for e in bm.edges:
    e.seam = True  # cut every edge -> 6 separate squares
bmesh.update_edit_mesh(obj.data)
live.on_depsgraph_update(scene, bpy.context.evaluated_depsgraph_get())
live._run()
print("all seams cut -> pieces:", props.info_islands, "pages:", props.info_pages)
assert props.info_islands == 6
bpy.ops.object.mode_set(mode="OBJECT")

# SVG export operator
props.live = False
bpy.ops.papercraft.export_svg(filepath=f"{OUT}/op_export.svg")
print("export files:", sorted(f for f in os.listdir(OUT) if f.startswith("op_export")))
addon.unregister()
print("BLENDER OK")
