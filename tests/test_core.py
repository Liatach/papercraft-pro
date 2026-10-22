"""Core checks without Blender: no overlaps, folds rebuild the 3D shape,
layout fits the pages."""
import itertools
import math
import os
import sys

import numpy as np

import types  # noqa: E402

# Import the core without running the add-on's bpy-dependent __init__.
_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "papercraft_pro")
_pkg = types.ModuleType("papercraft_pro")
_pkg.__path__ = [_root]
sys.modules.setdefault("papercraft_pro", _pkg)
from papercraft_pro.core import geom as g  # noqa: E402
from papercraft_pro.core.layout import compute_layout  # noqa: E402
from papercraft_pro.core.unfold import unfold  # noqa: E402


def cube():
    v = [(x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
    idx = {p: i for i, p in enumerate(v)}

    def q(*pts):
        return [idx[p] for p in pts]

    f = [
        q((-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)),
        q((-1, -1, -1), (-1, 1, -1), (1, 1, -1), (1, -1, -1)),
        q((1, -1, -1), (1, 1, -1), (1, 1, 1), (1, -1, 1)),
        q((-1, -1, -1), (-1, -1, 1), (-1, 1, 1), (-1, 1, -1)),
        q((-1, 1, -1), (-1, 1, 1), (1, 1, 1), (1, 1, -1)),
        q((-1, -1, -1), (1, -1, -1), (1, -1, 1), (-1, -1, 1)),
    ]
    return v, f


def uv_sphere(seg=16, rings=9):
    v = [(0, 0, 1)]
    for r in range(1, rings):
        th = math.pi * r / rings
        for s in range(seg):
            ph = 2 * math.pi * s / seg
            v.append((math.sin(th) * math.cos(ph), math.sin(th) * math.sin(ph), math.cos(th)))
    v.append((0, 0, -1))
    f = []
    ring = lambda r, s: 1 + (r - 1) * seg + (s % seg)  # noqa: E731
    for s in range(seg):
        f.append([0, ring(1, s), ring(1, s + 1)])
    for r in range(1, rings - 1):
        for s in range(seg):
            f.append([ring(r, s), ring(r + 1, s), ring(r + 1, s + 1), ring(r, s + 1)])
    last = len(v) - 1
    for s in range(seg):
        f.append([ring(rings - 1, s), last, ring(rings - 1, s + 1)])
    return v, f


def rot_about(point, axis, angle):
    a = np.asarray(axis, float)
    a /= np.linalg.norm(a)
    K = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
    R = np.eye(3) + math.sin(angle) * K + (1 - math.cos(angle)) * K @ K
    M = np.eye(4)
    M[:3, :3] = R
    p = np.asarray(point, float)
    M[:3, 3] = p - R @ p
    return M


def fold_island(res, isl, t=1.0):
    """Forward kinematics over the hinge tree; returns {(face, corner): xyz}."""
    M = {isl.root: np.eye(4)}
    out = {}
    for f in isl.order:
        if f != isl.root:
            p, i, j, ang = isl.parent[f]
            P = res.faces[p]
            pi = isl.faces[p][P.loop.index(i)]
            pj = isl.faces[p][P.loop.index(j)]
            axis = (pj[0] - pi[0], pj[1] - pi[1], 0.0)
            M[f] = M[p] @ rot_about((pi[0], pi[1], 0.0), axis, ang * t)
        for k, pt in enumerate(isl.faces[f]):
            out[(f, k)] = (M[f] @ np.array([pt[0], pt[1], 0.0, 1.0]))[:3]
    return out


def check(name, verts, faces):
    res = unfold(verts, faces)
    worst_overlap = 0
    for isl in res.islands:
        polys = list(isl.faces.items())
        for (fa, pa), (fb, pb) in itertools.combinations(polys, 2):
            if g.polygons_overlap(pa, pb, 1e-12):
                worst_overlap += 1
        # Fold back and compare all pairwise distances with the original.
        folded = fold_island(res, isl)
        keys = list(folded)
        orig = {key: np.array(verts[res.faces[key[0]].loop[key[1]]]) for key in keys}
        err = 0.0
        for a, b in itertools.combinations(keys, 2):
            d1 = np.linalg.norm(folded[a] - folded[b])
            d2 = np.linalg.norm(orig[a] - orig[b])
            err = max(err, abs(d1 - d2))
        assert err < 1e-6, f"{name}: island {isl.index} refold error {err}"
    assert worst_overlap == 0, f"{name}: {worst_overlap} overlapping face pairs"
    lay = compute_layout(res, scale_mode="FIT", max_pages=1, tab_height=5)
    folds = sum(1 for e in res.edges.values() if e.kind == "fold")
    print(
        f"{name:8s} faces={len(faces):4d} islands={len(res.islands):3d} folds={folds:4d} "
        f"glue pairs={res.glue_count:3d} scale={lay.scale:.2f}mm/unit "
        f"pages={lay.page_count} fits={lay.fits} msgs={lay.messages} warn={res.warnings}"
    )
    # Every placed island stays inside the printable area.
    for isl in res.islands:
        for f in isl.faces:
            for x, y in lay.face_points(res, isl, f):
                assert lay.margin - 1e-6 <= x <= lay.page_w - lay.margin + 1e-6, (name, x)
                assert lay.margin - 1e-6 <= y <= lay.page_h - lay.margin + 1e-6, (name, y)
    for t in lay.tabs:
        for x, y in lay.tab_points(t):
            assert lay.margin - 1e-6 <= x <= lay.page_w - lay.margin + 1e-6
            assert lay.margin - 1e-6 <= y <= lay.page_h - lay.margin + 1e-6
    # Fold-angle sign: a cube's edges are all convex -> mountain (+90 deg).
    return res, lay


if __name__ == "__main__":
    res, _ = check("cube", *cube())
    angles = {round(math.degrees(e.angle)) for e in res.edges.values()}
    assert angles == {90}, angles
    assert len(res.islands) == 1
    check("sphere", *uv_sphere())
    check("sphere64", *uv_sphere(48, 24))
    print("core OK")
