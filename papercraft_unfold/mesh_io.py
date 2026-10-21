# SPDX-License-Identifier: GPL-3.0-or-later
"""Read a Blender object into plain Python data for the unfolding core."""

import hashlib
import struct

import bmesh
import bpy

JOIN_ATTR = "papercraft_join"  # INT on edges: user forced this edge to fold
FLIP_ATTR = "papercraft_tab_flip"  # INT on edges: user flipped the tab side
COLOR_TOLERANCE = 0.08


class SourceData:
    __slots__ = (
        "verts", "polys", "seams", "joins", "flips", "materials", "uvs", "colors",
        "mat_list", "images", "signature", "topology_ok", "rule_cuts", "face_colors",
        "user_seams", "uv_layers", "render_uv", "color_layers", "active_color",
        "image_uvs",
    )

    def __init__(self):
        self.verts = []
        self.polys = []
        self.seams = []  # user seams + cuts from rules (materials, colours, sharp)
        self.joins = []
        self.flips = []
        self.materials = []
        self.uvs = None  # per polygon list of (u, v) or None
        self.colors = []  # per material slot (r, g, b) or None
        self.mat_list = []  # Material datablocks per slot
        self.images = []  # per material slot: bpy Image or None
        self.face_colors = None
        self.signature = ""
        self.topology_ok = True  # evaluated mesh matches the editable mesh
        self.rule_cuts = 0
        self.user_seams = set()  # normalised keys of edges marked as seams
        self.uv_layers = {}  # name -> per polygon [(u, v), ...] (all UV maps)
        self.render_uv = None
        self.color_layers = {}  # name -> per polygon [(r, g, b, a), ...]
        self.active_color = None
        self.image_uvs = []  # per material slot: UV map name its image uses


# ------------------------------------------------------------- materials
def _uv_of_image_node(node):
    """UV map named by a UV Map node feeding an image node's Vector input."""
    sock = node.inputs.get("Vector")
    if sock is None:
        return None
    for link in sock.links:
        n = link.from_node
        for _ in range(8):
            if n.type == "UVMAP":
                return n.uv_map or None
            ins = [s for s in n.inputs if s.links]
            if not ins:
                break
            n = ins[0].links[0].from_node
    return None


def _upstream_image(node, depth=0, seen=None):
    """First Image Texture node feeding `node`, looking through mix nodes and
    into node groups (layered setups such as Paint System keep their images
    inside groups). Returns (image, uv_map_name_or_None)."""
    if node is None or depth > 24:
        return None
    if seen is None:
        seen = set()
    ident = (node.id_data.name, node.name)
    if ident in seen:
        return None
    seen.add(ident)
    if node.type == "TEX_IMAGE" and node.image is not None:
        return node.image, _uv_of_image_node(node)
    if node.type == "GROUP" and node.node_tree is not None:
        for inner in node.node_tree.nodes:
            if inner.type == "GROUP_OUTPUT" and inner.is_active_output:
                found = _upstream_image(inner, depth + 1, seen)
                if found:
                    return found
    if node.type == "GROUP_INPUT":
        return None
    for sock in node.inputs:
        for link in sock.links:
            found = _upstream_image(link.from_node, depth + 1, seen)
            if found:
                return found
    return None


def material_image(mat):
    """(image, uv map name or None) a material prints with: whatever feeds
    Base Color (or the shader output), else any image texture node."""
    if mat is None or not mat.use_nodes or mat.node_tree is None:
        return None, None
    nodes = mat.node_tree.nodes
    for node in nodes:
        if node.type == "BSDF_PRINCIPLED":
            sock = node.inputs.get("Base Color")
            if sock is not None:
                for link in sock.links:
                    found = _upstream_image(link.from_node)
                    if found:
                        return found
    for node in nodes:
        if node.type == "OUTPUT_MATERIAL" and node.is_active_output:
            found = _upstream_image(node)
            if found:
                return found
    for node in nodes:
        if node.type == "TEX_IMAGE" and node.image is not None:
            return node.image, _uv_of_image_node(node)
    return None, None


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


# ------------------------------------------------------------- rules
def boundary_keys(polys, values, differ):
    """Edges between faces whose values differ."""
    owners = {}
    for fi, loop in enumerate(polys):
        count = len(loop)
        for k in range(count):
            a, b = loop[k], loop[(k + 1) % count]
            key = (a, b) if a < b else (b, a)
            owners.setdefault(key, []).append(fi)
    out = []
    for key, faces in owners.items():
        if len(faces) == 2 and differ(values[faces[0]], values[faces[1]]):
            out.append(key)
    return out


def _colors_differ(a, b):
    if a is None or b is None:
        return False
    return max(abs(a[i] - b[i]) for i in range(3)) > COLOR_TOLERANCE


def _avg(cols):
    n = len(cols)
    return tuple(sum(c[i] for c in cols) / n for i in range(3)) if n else None


def _active_color_info(me):
    """(name, domain, data_type) of the colour layer to read, if any."""
    attrs = getattr(me, "color_attributes", None)
    name = None
    if attrs is not None:
        name = getattr(attrs, "active_color_name", None)
        if not name and attrs.active_color is not None:
            name = attrs.active_color.name
    attr = me.attributes.get(name) if name else None
    if attr is None:  # fall back to the first colour-typed attribute
        for a in me.attributes:
            if a.data_type in ("FLOAT_COLOR", "BYTE_COLOR") and not a.name.startswith("."):
                attr = a
                break
    if attr is None or attr.data_type not in ("FLOAT_COLOR", "BYTE_COLOR"):
        return None
    return attr.name, attr.domain, attr.data_type


def _render_uv_name(me):
    for uv in me.uv_layers:
        if uv.active_render:
            return uv.name
    return me.uv_layers.active.name if me.uv_layers.active else None


# ------------------------------------------------------------- reading
def read_object(obj, depsgraph, rules=None):
    """World-space geometry.

    In Edit Mode the live edit mesh is read (so marking seams updates the
    layout immediately); otherwise the evaluated mesh is used, so modifiers
    such as Decimate are respected.

    rules: dict with optional bool keys 'seams', 'materials', 'colors',
    'sharp' choosing which edges become cuts.
    """
    rules = rules or {"seams": True}
    data = SourceData()
    mw = obj.matrix_world
    data.mat_list = [slot.material for slot in obj.material_slots]
    data.colors = [material_color(m) for m in data.mat_list]
    found = [material_image(m) for m in data.mat_list]
    data.images = [f[0] for f in found]
    color_info = _active_color_info(obj.data)
    uv_name = _render_uv_name(obj.data)
    data.render_uv = uv_name
    data.image_uvs = [f[1] or uv_name for f in found]
    data.active_color = color_info[0] if color_info else None
    seams, sharp = [], []

    if obj.mode == "EDIT" and obj.type == "MESH":
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.index_update()
        bm.faces.index_update()
        bm.edges.index_update()
        uv_layer = bm.loops.layers.uv.get(uv_name) if uv_name else None
        data.verts = [tuple(mw @ v.co) for v in bm.verts]
        data.polys = [[v.index for v in f.verts] for f in bm.faces]
        data.materials = [f.material_index for f in bm.faces]
        for e in bm.edges:
            key = (e.verts[0].index, e.verts[1].index)
            if e.seam:
                seams.append(key)
            if not e.smooth:
                sharp.append(key)
        for name, out in ((JOIN_ATTR, data.joins), (FLIP_ATTR, data.flips)):
            layer = bm.edges.layers.int.get(name)
            if layer is not None:
                out.extend(
                    (e.verts[0].index, e.verts[1].index) for e in bm.edges if e[layer]
                )
        for name in bm.loops.layers.uv.keys():
            layer = bm.loops.layers.uv[name]
            data.uv_layers[name] = [[tuple(lp[layer].uv) for lp in f.loops] for f in bm.faces]
        for kind in ("float_color", "color"):
            for name in getattr(bm.loops.layers, kind).keys():
                layer = getattr(bm.loops.layers, kind)[name]
                data.color_layers[name] = [[tuple(lp[layer]) for lp in f.loops] for f in bm.faces]
            for name in getattr(bm.verts.layers, kind).keys():
                layer = getattr(bm.verts.layers, kind)[name]
                data.color_layers[name] = [[tuple(v[layer]) for v in f.verts] for f in bm.faces]
        if uv_layer is not None:
            data.uvs = data.uv_layers.get(uv_name)
        if rules.get("colors") and color_info:
            data.face_colors = _bm_face_colors(bm, *color_info)
    else:
        eval_obj = obj.evaluated_get(depsgraph)
        mesh = eval_obj.to_mesh()
        try:
            orig = obj.data
            data.topology_ok = (
                len(mesh.vertices) == len(orig.vertices)
                and len(mesh.edges) == len(orig.edges)
                and len(mesh.polygons) == len(orig.polygons)
            )
            data.verts = [tuple(mw @ v.co) for v in mesh.vertices]
            data.polys = [list(p.vertices) for p in mesh.polygons]
            data.materials = [p.material_index for p in mesh.polygons]
            edge_keys = [tuple(e.vertices) for e in mesh.edges]
            seams = [k for k, e in zip(edge_keys, mesh.edges) if e.use_seam]
            sharp_attr = mesh.attributes.get("sharp_edge")
            if sharp_attr is not None and sharp_attr.domain == "EDGE":
                sharp = [k for k, d in zip(edge_keys, sharp_attr.data) if d.value]
            for name, out in ((JOIN_ATTR, data.joins), (FLIP_ATTR, data.flips)):
                attr = mesh.attributes.get(name)
                if attr is not None and attr.domain == "EDGE":
                    out.extend(k for k, d in zip(edge_keys, attr.data) if d.value)
            _read_corner_layers(mesh, data)
            data.uvs = data.uv_layers.get(uv_name) if uv_name else None
            if rules.get("colors") and color_info:
                data.face_colors = _mesh_face_colors(mesh, color_info[0])
        finally:
            eval_obj.to_mesh_clear()

    data.user_seams = {(a, b) if a < b else (b, a) for a, b in seams}
    cuts = list(seams) if rules.get("seams", True) else []
    extra = []
    if rules.get("sharp"):
        extra.extend(sharp)
    if rules.get("materials"):
        extra.extend(boundary_keys(data.polys, data.materials, lambda a, b: a != b))
    if rules.get("colors") and data.face_colors:
        extra.extend(boundary_keys(data.polys, data.face_colors, _colors_differ))
    # A user "join" overrides a rule cut (but not an explicit seam).
    joined = {(a, b) if a < b else (b, a) for a, b in data.joins}
    extra = [k for k in extra if ((k[0], k[1]) if k[0] < k[1] else (k[1], k[0])) not in joined]
    data.rule_cuts = len(set(extra))
    data.seams = cuts + extra

    h = hashlib.sha1()
    for v in data.verts:
        h.update(struct.pack("3d", *v))
    for p in data.polys:
        h.update(struct.pack(f"{len(p)}i", *p))
        h.update(b"|")
    for group in (data.seams, data.joins, data.flips):
        for s in group:
            h.update(struct.pack("2i", *s))
        h.update(b"#")
    h.update(bytes(str(data.materials), "ascii"))
    if data.uvs:
        h.update(struct.pack("4d", *data.uvs[0][0], *data.uvs[-1][-1]))
    if data.face_colors:
        h.update(bytes(str([None if c is None else tuple(round(x, 2) for x in c)
                            for c in data.face_colors]), "ascii"))
    data.signature = h.hexdigest()
    return data


def _read_corner_layers(mesh, data):
    """All UV maps and colour attributes, as per-polygon corner lists."""
    npoly = len(mesh.polygons)
    starts = [0] * npoly
    totals = [0] * npoly
    mesh.polygons.foreach_get("loop_start", starts)
    mesh.polygons.foreach_get("loop_total", totals)
    nloop = len(mesh.loops)
    for uv in mesh.uv_layers:
        flat = [0.0] * (nloop * 2)
        uv.data.foreach_get("uv", flat)
        data.uv_layers[uv.name] = [
            [(flat[2 * li], flat[2 * li + 1]) for li in range(st, st + tot)]
            for st, tot in zip(starts, totals)
        ]
    corner_vert = None
    for attr in mesh.attributes:
        if attr.data_type not in ("FLOAT_COLOR", "BYTE_COLOR") or attr.name.startswith("."):
            continue
        if attr.domain not in ("CORNER", "POINT", "FACE"):
            continue
        flat = [0.0] * (len(attr.data) * 4)
        attr.data.foreach_get("color", flat)
        vals = [tuple(flat[4 * i:4 * i + 4]) for i in range(len(attr.data))]
        if attr.domain == "CORNER":
            per = [vals[st:st + tot] for st, tot in zip(starts, totals)]
        elif attr.domain == "POINT":
            if corner_vert is None:
                corner_vert = [0] * nloop
                mesh.loops.foreach_get("vertex_index", corner_vert)
            per = [[vals[corner_vert[li]] for li in range(st, st + tot)]
                   for st, tot in zip(starts, totals)]
        else:
            per = [[vals[fi]] * tot for fi, tot in enumerate(totals)]
        data.color_layers[attr.name] = per


def _mesh_face_colors(mesh, name):
    attr = mesh.attributes.get(name)
    if attr is None:
        return None
    vals = [tuple(d.color[:3]) for d in attr.data]
    if attr.domain == "FACE":
        return vals
    if attr.domain == "CORNER":
        return [
            _avg(vals[p.loop_start:p.loop_start + p.loop_total]) for p in mesh.polygons
        ]
    if attr.domain == "POINT":
        return [_avg([vals[v] for v in p.vertices]) for p in mesh.polygons]
    return None


def _bm_face_colors(bm, name, domain, data_type):
    kind = "float_color" if data_type == "FLOAT_COLOR" else "color"
    try:
        if domain == "CORNER":
            layer = getattr(bm.loops.layers, kind).get(name)
            if layer is None:
                return None
            return [_avg([tuple(lp[layer][:3]) for lp in f.loops]) for f in bm.faces]
        if domain == "POINT":
            layer = getattr(bm.verts.layers, kind).get(name)
            if layer is None:
                return None
            return [_avg([tuple(v[layer][:3]) for v in f.verts]) for f in bm.faces]
        if domain == "FACE":
            layer = getattr(bm.faces.layers, kind).get(name)
            if layer is None:
                return None
            return [tuple(f[layer][:3]) for f in bm.faces]
    except (AttributeError, KeyError):
        return None
    return None


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
