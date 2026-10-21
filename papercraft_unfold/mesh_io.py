# SPDX-License-Identifier: GPL-3.0-or-later
"""Read a Blender object into plain Python data for the unfolding core."""

import hashlib
import struct

import bmesh
import bpy


class SourceData:
    __slots__ = ("verts", "polys", "seams", "materials", "uvs", "colors", "mat_list", "signature")

    def __init__(self):
        self.verts = []
        self.polys = []
        self.seams = []
        self.materials = []
        self.uvs = None  # per polygon list of (u, v) or None
        self.colors = []  # per material slot (r, g, b) or None
        self.mat_list = []  # Material datablocks per slot
        self.signature = ""


def material_color(mat):
    """Best guess at the colour a material shows: Principled base colour,
    falling back to the viewport display colour."""
    if mat is None:
        return None
    if mat.use_nodes and mat.node_tree:
        for node in mat.node_tree.nodes:
            if node.type == "BSDF_PRINCIPLED":
                sock = node.inputs.get("Base Color")
                if sock is not None and not sock.is_linked:
                    return tuple(sock.default_value[:3])
    return tuple(mat.diffuse_color[:3])


def read_object(obj, depsgraph):
    """World-space geometry. In Edit Mode the live edit mesh is read (so
    marking seams updates the layout immediately); otherwise the evaluated
    mesh is used, so modifiers such as Decimate are respected."""
    data = SourceData()
    mw = obj.matrix_world
    data.mat_list = [slot.material for slot in obj.material_slots]
    data.colors = [material_color(m) for m in data.mat_list]

    if obj.mode == "EDIT" and obj.type == "MESH":
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        uv_layer = bm.loops.layers.uv.active
        data.verts = [tuple(mw @ v.co) for v in bm.verts]
        data.polys = [[v.index for v in f.verts] for f in bm.faces]
        data.materials = [f.material_index for f in bm.faces]
        data.seams = [(e.verts[0].index, e.verts[1].index) for e in bm.edges if e.seam]
        if uv_layer is not None:
            data.uvs = [[tuple(lp[uv_layer].uv) for lp in f.loops] for f in bm.faces]
    else:
        eval_obj = obj.evaluated_get(depsgraph)
        mesh = eval_obj.to_mesh()
        try:
            data.verts = [tuple(mw @ v.co) for v in mesh.vertices]
            data.polys = [list(p.vertices) for p in mesh.polygons]
            data.materials = [p.material_index for p in mesh.polygons]
            data.seams = [tuple(e.vertices) for e in mesh.edges if e.use_seam]
            uv = mesh.uv_layers.active
            if uv is not None:
                uvd = uv.data
                data.uvs = [
                    [tuple(uvd[li].uv) for li in range(p.loop_start, p.loop_start + p.loop_total)]
                    for p in mesh.polygons
                ]
        finally:
            eval_obj.to_mesh_clear()

    h = hashlib.sha1()
    for v in data.verts:
        h.update(struct.pack("3d", *v))
    for p in data.polys:
        h.update(struct.pack(f"{len(p)}i", *p))
        h.update(b"|")
    for s in data.seams:
        h.update(struct.pack("2i", *s))
    h.update(bytes(str(data.materials), "ascii"))
    data.signature = h.hexdigest()
    return data


def ensure_collection(scene, name):
    coll = bpy.data.collections.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
    if coll.name not in scene.collection.children:
        try:
            scene.collection.children.link(coll)
        except RuntimeError:
            pass
    return coll
