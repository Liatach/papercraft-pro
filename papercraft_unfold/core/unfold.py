# SPDX-License-Identifier: GPL-3.0-or-later
"""Mesh unfolding: split a closed (or open) polygon mesh into flat,
non-overlapping islands connected by fold hinges.

Method (standard greedy approach for papercraft nets):
  1. Flatten every face into its own 2D frame (outside / printed side up).
  2. Sort candidate fold edges, flattest first, so flat regions stay whole.
  3. Kruskal-style merging: joining two islands across an edge rotates the
     smaller island into place; the join is refused if any faces overlap.
  4. Refused / cyclic edges become cuts (glued with tabs on the model).
  5. Each island gets a hinge tree (parent face, hinge edge, fold angle),
     which drives the fold animation and is reusable for pop-up rigs.

Pure Python, no bpy: vertices are (x, y, z) tuples in world space,
polygons are lists of vertex indices with consistent outward winding.
"""

import math
from collections import deque

from . import geom as g

KIND_FOLD = "fold"
KIND_CUT = "cut"  # glued to a partner edge somewhere else
KIND_BOUNDARY = "boundary"  # open mesh edge, nothing to glue to


class Face:
    __slots__ = (
        "index", "loop", "normal", "centroid", "local", "area", "material", "source", "corners",
    )

    def __init__(self, index, loop, verts, material=0, source=None, corners=None):
        self.index = index
        self.loop = list(loop)
        self.material = material
        # Which source polygon (and which of its corners) this face came from;
        # differs from index/loop when a non-planar face was triangulated.
        self.source = index if source is None else source
        self.corners = list(range(len(loop))) if corners is None else list(corners)
        pts = [verts[i] for i in loop]
        self.normal = g.newell_normal(pts)
        count = len(pts)
        self.centroid = (
            sum(p[0] for p in pts) / count,
            sum(p[1] for p in pts) / count,
            sum(p[2] for p in pts) / count,
        )
        # Local 2D frame: x along the first non-degenerate edge, y = n x u.
        u = (0.0, 0.0, 0.0)
        for k in range(count):
            u = g.norm3(g.sub3(pts[(k + 1) % count], pts[k]))
            if g.len3(u) > 0.5:
                break
        w = g.cross3(self.normal, u)
        origin = pts[0]
        self.local = [
            (g.dot3(g.sub3(p, origin), u), g.dot3(g.sub3(p, origin), w)) for p in pts
        ]
        self.area = g.polygon_area(self.local)

    def corner_of(self, a, b):
        """Index k such that loop[k] -> loop[k+1] is the edge {a, b}."""
        count = len(self.loop)
        for k in range(count):
            i, j = self.loop[k], self.loop[(k + 1) % count]
            if (i == a and j == b) or (i == b and j == a):
                return k
        raise KeyError((a, b))


class EdgeInfo:
    __slots__ = ("key", "uses", "kind", "angle", "number", "forced_cut")

    def __init__(self, key):
        self.key = key
        self.uses = []  # [(face_index, corner)]
        self.kind = KIND_BOUNDARY
        self.angle = 0.0  # signed fold angle, + = mountain (convex)
        self.number = 0  # glue number for cut pairs
        self.forced_cut = False


class Island:
    __slots__ = ("index", "faces", "bboxes", "bbox", "fold_keys", "root", "parent", "order")

    def __init__(self, index, face, coords):
        self.index = index
        self.faces = {face: coords}
        self.bboxes = {face: g.bbox(coords)}
        self.bbox = self.bboxes[face]
        self.fold_keys = []
        self.root = face
        # parent[f] = (parent_face, hinge_from_vertex, hinge_to_vertex, angle)
        self.parent = {}
        self.order = [face]  # faces in tree (BFS) order, root first

    def transform(self, t):
        for f, pts in self.faces.items():
            new = [t.apply(p) for p in pts]
            self.faces[f] = new
            self.bboxes[f] = g.bbox(new)
        self._update_bbox()

    def _update_bbox(self):
        boxes = list(self.bboxes.values())
        self.bbox = (
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        )

    def absorb(self, other):
        self.faces.update(other.faces)
        self.bboxes.update(other.bboxes)
        self.fold_keys.extend(other.fold_keys)
        self._update_bbox()


class UnfoldResult:
    def __init__(self):
        self.verts = []
        self.faces = []  # list[Face | None] indexed like the source polygons
        self.edges = {}  # key -> EdgeInfo
        self.islands = []
        self.warnings = []
        self.glue_count = 0

    def island_outline_points(self, island):
        pts = []
        for coords in island.faces.values():
            pts.extend(coords)
        return pts


def edge_key(a, b):
    return (a, b) if a < b else (b, a)


def nonplanarity(points):
    """Largest distance from the best-fit plane, relative to face size."""
    n = g.newell_normal(points)
    count = len(points)
    c = (
        sum(p[0] for p in points) / count,
        sum(p[1] for p in points) / count,
        sum(p[2] for p in points) / count,
    )
    size = max(g.len3(g.sub3(p, c)) for p in points) or 1.0
    return max(abs(g.dot3(g.sub3(p, c), n)) for p in points) / size


def _triangulate(points):
    """Corner-index triangles for a (possibly concave) polygon: ear clipping
    in its own plane; quads use the shorter diagonal."""
    count = len(points)
    if count == 4:
        d02 = g.len3(g.sub3(points[0], points[2]))
        d13 = g.len3(g.sub3(points[1], points[3]))
        return [(0, 1, 2), (0, 2, 3)] if d02 <= d13 else [(1, 2, 3), (1, 3, 0)]
    n = g.newell_normal(points)
    u = g.norm3(g.sub3(points[1], points[0]))
    w = g.cross3(n, u)
    flat = [(g.dot3(p, u), g.dot3(p, w)) for p in points]
    idx = list(range(count))
    tris = []
    guard = 0
    while len(idx) > 3 and guard < count * count:
        guard += 1
        m = len(idx)
        for k in range(m):
            a, b, c = idx[k - 1], idx[k], idx[(k + 1) % m]
            if g.cross2(g.sub2(flat[b], flat[a]), g.sub2(flat[c], flat[a])) <= 0:
                continue  # reflex corner
            tri = [flat[a], flat[b], flat[c]]
            if any(
                g.point_in_polygon(flat[o], tri) for o in idx if o not in (a, b, c)
            ):
                continue
            tris.append((a, b, c))
            idx.pop(k)
            break
        else:
            break
    if len(idx) >= 3:
        for k in range(1, len(idx) - 1):
            tris.append((idx[0], idx[k], idx[k + 1]))
    return tris


def unfold(
    verts, polygons, seams=(), materials=None, priority="flat", max_faces=0,
    planar_tolerance=0.002, joins=(),
):
    """Unfold a mesh.

    verts      list of (x, y, z) world-space positions
    polygons   list of vertex-index loops (outward winding)
    seams      iterable of (a, b) vertex pairs that must be cut
    materials  optional list of material indices per polygon
    priority   'flat'  keep flat regions together (default)
               'long'  keep long edges folded (fewer, stringier islands)
    max_faces  cap on faces per island (0 = no cap); smaller pieces leave
               more room for tabs on round models
    planar_tolerance  faces bent out of plane by more than this fraction of
               their size are split into triangles (paper cannot bend)
    joins      (a, b) pairs the user wants kept folded; they are tried first
               and only cut if joining would make pieces overlap
    """
    res = UnfoldResult()
    res.verts = list(verts)
    seam_keys = {edge_key(a, b) for a, b in seams}
    join_keys = {edge_key(a, b) for a, b in joins} - seam_keys

    # Size-relative tolerances.
    if verts:
        xs, ys, zs = zip(*verts)
        size = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs), 1e-6)
    else:
        size = 1.0
    eps_len = size * 1e-7
    eps_cross = (size * 1e-6) ** 2

    # 1. Faces (non-planar ones split into triangles)
    degenerate = split = 0
    for src_idx, loop in enumerate(polygons):
        mat = materials[src_idx] if materials else 0
        pieces = [list(range(len(loop)))]
        if len(loop) > 3:
            pts = [res.verts[i] for i in loop]
            if nonplanarity(pts) > planar_tolerance:
                pieces = [list(t) for t in _triangulate(pts)]
                split += 1
        for corners in pieces:
            sub_loop = [loop[c] for c in corners]
            face = Face(len(res.faces), sub_loop, res.verts, mat, src_idx, corners)
            if len(sub_loop) < 3 or abs(face.area) < eps_len * eps_len:
                res.faces.append(None)
                degenerate += 1
                continue
            res.faces.append(face)
    if degenerate:
        res.warnings.append(f"{degenerate} zero-area face(s) skipped")
    if split:
        res.warnings.append(f"{split} non-flat face(s) split into triangles")

    # 2. Edges
    for face in res.faces:
        if face is None:
            continue
        count = len(face.loop)
        for k in range(count):
            key = edge_key(face.loop[k], face.loop[(k + 1) % count])
            info = res.edges.get(key)
            if info is None:
                info = res.edges[key] = EdgeInfo(key)
            info.uses.append((face.index, k))

    candidates = []
    flipped = nonmanifold = 0
    for key, info in res.edges.items():
        if len(info.uses) != 2:
            if len(info.uses) > 2:
                nonmanifold += 1
                info.kind = KIND_CUT
            continue
        (fa, ka), (fb, kb) = info.uses
        A, B = res.faces[fa], res.faces[fb]
        ai, aj = A.loop[ka], A.loop[(ka + 1) % len(A.loop)]
        bi, bj = B.loop[kb], B.loop[(kb + 1) % len(B.loop)]
        info.kind = KIND_CUT
        if not (ai == bj and aj == bi):
            flipped += 1  # inconsistent normals: cannot fold flat-consistently
            continue
        d = g.norm3(g.sub3(res.verts[aj], res.verts[ai]))
        info.angle = math.atan2(
            g.dot3(g.cross3(A.normal, B.normal), d), g.dot3(A.normal, B.normal)
        )
        if key in seam_keys:
            info.forced_cut = True
            continue
        length = g.len3(g.sub3(res.verts[aj], res.verts[ai]))
        forced = 0 if key in join_keys else 1
        if priority == "long":
            weight = (forced, -length, abs(info.angle))
        else:
            weight = (forced, round(abs(info.angle), 4), -length)
        candidates.append((weight, key))
    if flipped:
        res.warnings.append(
            f"{flipped} edge(s) between faces with flipped normals were cut "
            "(Mesh > Normals > Recalculate Outside fixes this)"
        )
    if nonmanifold:
        res.warnings.append(f"{nonmanifold} non-manifold edge(s) were cut")

    # 3. Greedy merge
    islands = {}
    island_of = {}
    for face in res.faces:
        if face is None:
            continue
        isl = Island(face.index, face.index, list(face.local))
        islands[face.index] = isl
        island_of[face.index] = isl

    candidates.sort()
    for _, key in candidates:
        info = res.edges[key]
        (fa, ka), (fb, kb) = info.uses
        IA, IB = island_of[fa], island_of[fb]
        if IA is IB:
            continue  # closing a loop: stays a cut
        if max_faces and len(IA.faces) + len(IB.faces) > max_faces:
            continue
        # Move the smaller island onto the larger one.
        if len(IB.faces) > len(IA.faces):
            fa, ka, fb, kb = fb, kb, fa, ka
            IA, IB = IB, IA
        A, B = res.faces[fa], res.faces[fb]
        pa = IA.faces[fa]
        pb = IB.faces[fb]
        na, nb = len(A.loop), len(B.loop)
        # A traverses i->j at corner ka; B traverses j->i at corner kb.
        a_i, a_j = pa[ka], pa[(ka + 1) % na]
        b_j, b_i = pb[kb], pb[(kb + 1) % nb]
        t = g.Rigid2D.align_segment(b_i, b_j, a_i, a_j)
        if _would_overlap(IA, IB, t, eps_len, eps_cross):
            continue
        IB.transform(t)
        IA.absorb(IB)
        IA.fold_keys.append(key)
        for f in IB.faces:
            island_of[f] = IA
        info.kind = KIND_FOLD

    refused = sum(
        1 for k in join_keys if k in res.edges and res.edges[k].kind != KIND_FOLD
    )
    if refused:
        res.warnings.append(f"{refused} joined edge(s) had to stay cut (pieces would overlap)")

    # 4. Hinge trees
    unique = []
    seen = set()
    for isl in island_of.values():
        if id(isl) not in seen:
            seen.add(id(isl))
            unique.append(isl)
    unique.sort(key=lambda i: -len(i.faces))
    for n, isl in enumerate(unique):
        isl.index = n
        _build_tree(res, isl)
    res.islands = unique

    # 5. Glue numbers for cut pairs
    number = 0
    for isl in unique:
        for f in isl.order:
            face = res.faces[f]
            count = len(face.loop)
            for k in range(count):
                info = res.edges[edge_key(face.loop[k], face.loop[(k + 1) % count])]
                if info.kind == KIND_CUT and len(info.uses) == 2 and info.number == 0:
                    number += 1
                    info.number = number
    res.glue_count = number
    return res


def _would_overlap(IA, IB, t, eps_len, eps_cross):
    moved = {f: [t.apply(p) for p in pts] for f, pts in IB.faces.items()}
    boxes = {f: g.bbox(pts) for f, pts in moved.items()}
    all_b = (
        min(b[0] for b in boxes.values()),
        min(b[1] for b in boxes.values()),
        max(b[2] for b in boxes.values()),
        max(b[3] for b in boxes.values()),
    )
    if not g.bbox_overlap(IA.bbox, all_b, eps_len):
        return False
    a_items = [
        (f, IA.bboxes[f]) for f in IA.faces if g.bbox_overlap(IA.bboxes[f], all_b, eps_len)
    ]
    for fb, pb in moved.items():
        bb = boxes[fb]
        for fa, ba in a_items:
            if g.bbox_overlap(ba, bb, eps_len) and g.polygons_overlap(
                IA.faces[fa], pb, eps_cross
            ):
                return True
    return False


def _build_tree(res, isl):
    """BFS over fold edges from the largest face; records hinge + angle."""
    adjacency = {f: [] for f in isl.faces}
    for key in isl.fold_keys:
        (fa, _), (fb, _) = res.edges[key].uses
        adjacency[fa].append((fb, key))
        adjacency[fb].append((fa, key))
    root = max(isl.faces, key=lambda f: abs(res.faces[f].area))
    isl.root = root
    isl.parent = {}
    isl.order = [root]
    queue = deque([root])
    visited = {root}
    while queue:
        p = queue.popleft()
        P = res.faces[p]
        for c, key in adjacency[p]:
            if c in visited:
                continue
            visited.add(c)
            k = P.corner_of(*key)
            i, j = P.loop[k], P.loop[(k + 1) % len(P.loop)]
            isl.parent[c] = (p, i, j, res.edges[key].angle)
            isl.order.append(c)
            queue.append(c)


def face_corner_edges(res, face_index):
    """Yield (corner, EdgeInfo) for a face."""
    face = res.faces[face_index]
    count = len(face.loop)
    for k in range(count):
        yield k, res.edges[edge_key(face.loop[k], face.loop[(k + 1) % count])]
