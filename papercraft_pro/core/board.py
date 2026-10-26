# SPDX-License-Identifier: GPL-3.0-or-later
"""Thick materials: card, greyboard and corrugated board.

The model is the outside (printed) surface; the board's thickness lies
behind it. Two things change from paper:

Folds
  CREASE   the board bends round a radius. Its outside surface runs round
           the arc instead of the sharp model corner, which is shorter, so
           each piece is shortened at every fold by R*(2*tan(a/2) - a)
           (R = radius of the printed surface at that fold, a = fold angle).
           Done exactly by sliding each branch of a piece's hinge tree
           towards its hinge (pieces stay rigid; see apply_bend_allowance).
  VGROOVE  the printed liner folds sharp on the line and a V is carved out
           of the back, 2*t*tan(a/2) wide (corrugated board). The front
           pattern is the model's exact size; the grooves are marked.
           Only outward (mountain) folds can be grooved: folding inward,
           the faces would have to spread apart by the groove width, which
           can't stay flat where several such folds meet. So inward folds
           in thick board are cut (Cut Valley Folds) or left as creases.

Joins (two pieces meeting along a seam)
  TABS     glue tabs, as for paper.
  MITRE    edge to edge. At an outside corner the two boards would collide
           behind the surface, so each back is bevelled away by
           t*tan(a/2) from the edge (a 45 degree mitre on a box). Inside
           corners meet on their printed edges with nothing to carve.

Pure Python; no Blender.
"""

import math

from . import geom as g
from .unfold import KIND_CUT, KIND_FOLD, edge_key

MAX_ANGLE = math.radians(170.0)  # tan() runs away beyond this

# name: (label, thickness mm, fold style, fold radius mm, join style,
#        cut valley folds, description)
PRESETS = {
    "CARDSTOCK": ("Cardstock", 0.3, "CREASE", 0.0, "TABS", False,
                  "Card and paper (up to ~0.4 mm): crease folds, glue tabs"),
    "CARDBOARD": ("Cardboard", 1.5, "CREASE", 1.5, "TABS", False,
                  "Greyboard / chipboard (~1.5 mm): creased folds round a radius, "
                  "pieces shortened to suit"),
    "FLUTE3": ("Corrugated 3 mm", 3.0, "VGROOVE", 0.0, "MITRE", True,
               "3 mm flute (E/B): V-grooved folds, mitred joins, no tabs"),
    "FLUTE5": ("Corrugated 5 mm", 5.0, "VGROOVE", 0.0, "MITRE", True,
               "5 mm flute (C): V-grooved folds, mitred joins, no tabs"),
    "FLUTE10": ("Corrugated 10 mm", 10.0, "VGROOVE", 0.0, "MITRE", True,
                "10 mm double wall: V-grooved folds, mitred joins, no tabs"),
}


def _clamp(angle):
    return max(-MAX_ANGLE, min(MAX_ANGLE, angle))


def groove_width(thickness, angle):
    """Width of the V at the grooved face for a fold of `angle` radians."""
    return 2.0 * thickness * math.tan(abs(_clamp(angle)) / 2.0)


def mitre_inset(thickness, angle):
    """How far the back is bevelled away from the edge of an outside corner."""
    return thickness * math.tan(abs(_clamp(angle)) / 2.0)


def bend_shortening(radius, angle):
    """How much shorter the printed surface is round a fold of `radius`."""
    a = abs(_clamp(angle))
    return max(0.0, radius * (2.0 * math.tan(a / 2.0) - a))


def apply_bend_allowance(res, scale, radius_mm, thickness_mm, min_angle=0.0):
    """Shorten every piece at its creased folds (CREASE style).

    radius_mm is the outside radius of the bend. At a mountain fold the
    printed surface is that outside; at a valley it's the inside, radius
    minus the thickness. Each child face (and everything hanging off it)
    slides towards its hinge by the shortening, so the faces stay rigid and
    only overlap by that sliver along the crease. Safe to call again: the
    original flat shapes are kept on `res` and restored first.
    """
    original = getattr(res, "flat_original", None)
    if original is None:
        original = res.flat_original = {
            isl.index: {f: list(pts) for f, pts in isl.faces.items()} for isl in res.islands
        }
    for isl in res.islands:
        base = original.get(isl.index)
        if base is None:
            continue
        offset = {isl.root: (0.0, 0.0)}
        for f in isl.order:
            if f == isl.root:
                continue
            p, i, j, angle = isl.parent[f]
            ox, oy = offset[p]
            shift = 0.0
            if radius_mm > 0.0 and scale > 0.0 and abs(angle) > min_angle:
                front_r = radius_mm if angle > 0 else max(radius_mm - thickness_mm, 0.0)
                shift = bend_shortening(front_r, angle) / scale  # model units
            if shift:
                P = res.faces[p]
                a = base[p][P.loop.index(i)]
                b = base[p][P.loop.index(j)]
                d = g.sub2(b, a)
                ln = g.len2(d)
                if ln > 1e-12:
                    n = (-d[1] / ln, d[0] / ln)
                    c = g.polygon_centroid(base[f])
                    if g.dot2(g.sub2(c, a), n) < 0:
                        n = (-n[0], -n[1])  # point into the child
                    ox, oy = ox - n[0] * shift, oy - n[1] * shift
            offset[f] = (ox, oy)
        for f, pts in base.items():
            dx, dy = offset.get(f, (0.0, 0.0))
            moved = [(x + dx, y + dy) for x, y in pts]
            isl.faces[f] = moved
            isl.bboxes[f] = g.bbox(moved)
        isl._update_bbox()


def clear_bend_allowance(res):
    """Put the exact (model-sized) flat shapes back."""
    if getattr(res, "flat_original", None) is not None:
        apply_bend_allowance(res, 1.0, 0.0, 0.0)


def _clip_line(poly, p, d):
    """Part of the infinite line p + s*d inside convex polygon `poly`
    (Cyrus-Beck). Returns (start, end) or None."""
    n = len(poly)
    area = sum(poly[k][0] * poly[(k + 1) % n][1] - poly[(k + 1) % n][0] * poly[k][1]
               for k in range(n))
    lo, hi = -1e18, 1e18
    for k in range(n):
        a, b = poly[k], poly[(k + 1) % n]
        e = g.sub2(b, a)
        inward = (-e[1], e[0]) if area > 0 else (e[1], -e[0])
        num = g.dot2(g.sub2(p, a), inward)
        den = g.dot2(d, inward)
        if abs(den) < 1e-12:
            if num < 0:
                return None
            continue
        s = -num / den
        if den > 0:
            lo = max(lo, s)
        else:
            hi = min(hi, s)
    if hi - lo < 1e-9:
        return None
    return (g.add2(p, g.mul2(d, lo)), g.add2(p, g.mul2(d, hi)))


def _inset_line(face_pts, a, b, dist):
    """The line `dist` inside the face, parallel to its edge a->b, clipped
    to the face."""
    d = g.sub2(b, a)
    ln = g.len2(d)
    if ln < 1e-9:
        return None
    n = (-d[1] / ln, d[0] / ln)
    c = g.polygon_centroid(face_pts)
    if g.dot2(g.sub2(c, a), n) < 0:
        n = (-n[0], -n[1])
    return _clip_line(face_pts, g.add2(a, g.mul2(n, dist)), g.mul2(d, 1.0 / ln))


def board_marks(res, lay, thickness, fold_style, join_style, min_angle=0.0):
    """Per page: {"groove_back": [...], "groove_centre": [...],
    "bevel_back": [...]}, each a list of segments in page mm (y up, front
    view), plus "valley_folds": how many inward folds are left uncut.

    groove_back   the two edges of each V-groove (mountain folds)
    groove_centre the fold line itself
    bevel_back    where the back of each piece stops at an outside-corner mitre
    """
    pages = [{"groove_back": [], "groove_centre": [], "bevel_back": [], "valley_folds": 0}
             for _ in range(max(lay.page_count, 1))]
    if thickness <= 0.0:
        return pages
    t_mm = thickness
    for isl in res.islands:
        page = pages[lay.placements[isl.index].page]
        pts_of = {f: lay.face_points(res, isl, f) for f in isl.faces}
        done = set()
        for f in isl.order:
            face = res.faces[f]
            pts = pts_of[f]
            n = len(pts)
            for k in range(n):
                key = edge_key(face.loop[k], face.loop[(k + 1) % n])
                info = res.edges[key]
                a, b = pts[k], pts[(k + 1) % n]
                if abs(info.angle) <= min_angle:
                    continue
                if info.kind == KIND_FOLD and fold_style == "VGROOVE":
                    if info.angle < 0:
                        if key not in done:
                            done.add(key)
                            page["valley_folds"] += 1
                        continue
                    # Each face carries its own half of the groove.
                    half = groove_width(t_mm, info.angle) / 2.0
                    seg = _inset_line(pts, a, b, half)
                    if seg is not None:
                        page["groove_back"].append(seg)
                    if key not in done:
                        done.add(key)
                        page["groove_centre"].append((a, b))
                elif (info.kind == KIND_CUT and join_style == "MITRE"
                      and len(info.uses) == 2 and info.angle > 0):
                    seg = _inset_line(pts, a, b, mitre_inset(t_mm, info.angle))
                    if seg is not None:
                        page["bevel_back"].append(seg)
    return pages
