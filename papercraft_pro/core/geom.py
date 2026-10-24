# SPDX-License-Identifier: GPL-3.0-or-later
"""Small, dependency-free 2D/3D geometry helpers used by the unfolder.

Kept free of bpy/mathutils so the core can be unit-tested outside Blender.
"""

import math

EPS = 1e-9


# ---------------------------------------------------------------- 3D vectors
def sub3(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def add3(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def mul3(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def dot3(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross3(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def len3(a):
    return math.sqrt(dot3(a, a))


def norm3(a):
    length = len3(a)
    if length < EPS:
        return (0.0, 0.0, 0.0)
    return (a[0] / length, a[1] / length, a[2] / length)


def newell_normal(points):
    """Robust polygon normal (works for concave / slightly non-planar ngons)."""
    nx = ny = nz = 0.0
    count = len(points)
    for i in range(count):
        x1, y1, z1 = points[i]
        x2, y2, z2 = points[(i + 1) % count]
        nx += (y1 - y2) * (z1 + z2)
        ny += (z1 - z2) * (x1 + x2)
        nz += (x1 - x2) * (y1 + y2)
    return norm3((nx, ny, nz))


# ---------------------------------------------------------------- 2D helpers
def sub2(a, b):
    return (a[0] - b[0], a[1] - b[1])


def add2(a, b):
    return (a[0] + b[0], a[1] + b[1])


def mul2(a, s):
    return (a[0] * s, a[1] * s)


def dot2(a, b):
    return a[0] * b[0] + a[1] * b[1]


def cross2(a, b):
    return a[0] * b[1] - a[1] * b[0]


def len2(a):
    return math.hypot(a[0], a[1])


def rot2(p, c, s):
    return (p[0] * c - p[1] * s, p[0] * s + p[1] * c)


class Rigid2D:
    """2D rotation + translation (no reflection, no scale)."""

    __slots__ = ("c", "s", "tx", "ty")

    def __init__(self, angle=0.0, tx=0.0, ty=0.0):
        self.c = math.cos(angle)
        self.s = math.sin(angle)
        self.tx = tx
        self.ty = ty

    @property
    def angle(self):
        return math.atan2(self.s, self.c)

    def apply(self, p):
        return (
            p[0] * self.c - p[1] * self.s + self.tx,
            p[0] * self.s + p[1] * self.c + self.ty,
        )

    @staticmethod
    def align_segment(src_a, src_b, dst_a, dst_b):
        """Transform that maps segment src_a->src_b onto dst_a->dst_b."""
        ds = sub2(src_b, src_a)
        dd = sub2(dst_b, dst_a)
        angle = math.atan2(dd[1], dd[0]) - math.atan2(ds[1], ds[0])
        t = Rigid2D(angle)
        ra = t.apply(src_a)
        t.tx = dst_a[0] - ra[0]
        t.ty = dst_a[1] - ra[1]
        return t


def polygon_area(poly):
    area = 0.0
    count = len(poly)
    for i in range(count):
        area += cross2(poly[i], poly[(i + 1) % count])
    return area * 0.5


def polygon_centroid(poly):
    count = len(poly)
    return (sum(p[0] for p in poly) / count, sum(p[1] for p in poly) / count)


def bbox(poly):
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return (min(xs), min(ys), max(xs), max(ys))


def bbox_overlap(a, b, eps):
    return a[0] < b[2] - eps and b[0] < a[2] - eps and a[1] < b[3] - eps and b[1] < a[3] - eps


def point_in_polygon(pt, poly):
    """Even-odd ray cast. Points on the boundary are unreliable by design;
    callers only feed points that are strictly interior to their own polygon."""
    x, y = pt
    inside = False
    count = len(poly)
    j = count - 1
    for i in range(count):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            x_cross = xi + (y - yi) * (xj - xi) / (yj - yi)
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def interior_point(poly):
    """A point strictly inside a simple polygon (centroid when that works,
    otherwise the centroid of an ear-ish triangle)."""
    c = polygon_centroid(poly)
    if point_in_polygon(c, poly):
        return c
    count = len(poly)
    for i in range(count):
        a, b, d = poly[i - 1], poly[i], poly[(i + 1) % count]
        tc = ((a[0] + b[0] + d[0]) / 3.0, (a[1] + b[1] + d[1]) / 3.0)
        if point_in_polygon(tc, poly):
            return tc
    return c


def _orient(a, b, c):
    return cross2(sub2(b, a), sub2(c, a))


def segments_cross(p1, p2, q1, q2, eps):
    """True only for a *proper* crossing. Touching, sharing endpoints or
    running collinear along each other does not count (shared hinge edges
    and shared vertices are expected between neighbouring faces)."""
    d1 = _orient(q1, q2, p1)
    d2 = _orient(q1, q2, p2)
    d3 = _orient(p1, p2, q1)
    d4 = _orient(p1, p2, q2)
    return ((d1 > eps and d2 < -eps) or (d1 < -eps and d2 > eps)) and (
        (d3 > eps and d4 < -eps) or (d3 < -eps and d4 > eps)
    )


def polygons_overlap(pa, pb, eps):
    """Do two simple polygons share interior area?"""
    na, nb = len(pa), len(pb)
    for i in range(na):
        a1, a2 = pa[i], pa[(i + 1) % na]
        for j in range(nb):
            if segments_cross(a1, a2, pb[j], pb[(j + 1) % nb], eps):
                return True
    if point_in_polygon(interior_point(pa), pb):
        return True
    if point_in_polygon(interior_point(pb), pa):
        return True
    return False


def convex_hull(points):
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def half(seq):
        out = []
        for p in seq:
            while len(out) >= 2 and _orient(out[-2], out[-1], p) <= 0:
                out.pop()
            out.append(p)
        return out

    lower = half(pts)
    upper = half(reversed(pts))
    return lower[:-1] + upper[:-1]


def min_area_rect_angle(points):
    """Rotation angle that makes the axis-aligned bounding box of `points`
    as small as possible (rotating calipers over hull edges)."""
    hull = convex_hull(points)
    if len(hull) < 3:
        return 0.0
    best_angle, best_area = 0.0, float("inf")
    for i in range(len(hull)):
        e = sub2(hull[(i + 1) % len(hull)], hull[i])
        if len2(e) < EPS:
            continue
        angle = -math.atan2(e[1], e[0])
        c, s = math.cos(angle), math.sin(angle)
        rotated = [rot2(p, c, s) for p in hull]
        b = bbox(rotated)
        area = (b[2] - b[0]) * (b[3] - b[1])
        if area < best_area - EPS:
            best_area, best_angle = area, angle
    return best_angle


def triangulate_2d(poly):
    """Ear-clipping triangulation of a simple polygon (either winding).
    Returns corner-index triples."""
    count = len(poly)
    if count < 3:
        return []
    if count == 3:
        return [(0, 1, 2)]
    sign = 1.0 if polygon_area(poly) >= 0 else -1.0
    idx = list(range(count))
    tris = []
    guard = 0
    while len(idx) > 3 and guard < count * count:
        guard += 1
        m = len(idx)
        for k in range(m):
            a, b, c = idx[k - 1], idx[k], idx[(k + 1) % m]
            if sign * cross2(sub2(poly[b], poly[a]), sub2(poly[c], poly[a])) <= 0:
                continue  # reflex or degenerate corner
            tri = [poly[a], poly[b], poly[c]]
            if any(point_in_polygon(poly[o], tri) for o in idx if o not in (a, b, c)):
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


def affine_from_triangles(src, dst):
    """2x3 affine (a, b, c, d, e, f) mapping triangle src onto dst, in SVG
    matrix order: x' = a*x + c*y + e, y' = b*x + d*y + f. None if degenerate."""
    (x0, y0), (x1, y1), (x2, y2) = src
    det = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
    if abs(det) < 1e-12:
        return None
    (u0, v0), (u1, v1), (u2, v2) = dst
    a = ((u1 - u0) * (y2 - y0) - (u2 - u0) * (y1 - y0)) / det
    c = ((u2 - u0) * (x1 - x0) - (u1 - u0) * (x2 - x0)) / det
    b = ((v1 - v0) * (y2 - y0) - (v2 - v0) * (y1 - y0)) / det
    d = ((v2 - v0) * (x1 - x0) - (v1 - v0) * (x2 - x0)) / det
    e = u0 - a * x0 - c * y0
    f = v0 - b * x0 - d * y0
    return (a, b, c, d, e, f)


def grow_convex(poly, amount, miter=3.0):
    """Move every edge of a convex polygon outwards by `amount` (print
    bleed). Sharp corners are capped at `miter` x amount."""
    n = len(poly)
    if n < 3 or amount <= 0.0:
        return list(poly)
    area = sum(poly[k][0] * poly[(k + 1) % n][1] - poly[(k + 1) % n][0] * poly[k][1]
               for k in range(n))
    sign = 1.0 if area > 0 else -1.0  # outward normal is right of each edge for CCW
    out = []
    for k in range(n):
        p0, p1, p2 = poly[k - 1], poly[k], poly[(k + 1) % n]
        d1 = sub2(p1, p0)
        d2 = sub2(p2, p1)
        l1, l2 = len2(d1), len2(d2)
        if l1 < EPS or l2 < EPS:
            out.append(p1)
            continue
        n1 = (d1[1] / l1 * sign, -d1[0] / l1 * sign)
        n2 = (d2[1] / l2 * sign, -d2[0] / l2 * sign)
        bis = add2(n1, n2)
        bl = len2(bis)
        if bl < EPS:
            out.append(add2(p1, mul2(n1, amount)))
            continue
        cos_half = bl / 2.0
        reach = min(amount / max(cos_half, EPS), amount * miter)
        out.append(add2(p1, mul2(bis, reach / bl)))
    return out


def grow_triangle(tri, amount):
    """Push each corner of a triangle outwards so its edges move out by
    roughly `amount` (used for print bleed)."""
    cx = (tri[0][0] + tri[1][0] + tri[2][0]) / 3.0
    cy = (tri[0][1] + tri[1][1] + tri[2][1]) / 3.0
    out = []
    for p in tri:
        d = (p[0] - cx, p[1] - cy)
        length = len2(d)
        if length < EPS:
            out.append(p)
            continue
        out.append((p[0] + d[0] / length * amount * 2.0, p[1] + d[1] / length * amount * 2.0))
    return out
