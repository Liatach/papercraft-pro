# SPDX-License-Identifier: GPL-3.0-or-later
"""Page layout: scale islands to paper millimetres, add glue tabs,
and pack everything onto a set of standard-size pages."""

import math

from . import geom as g
from .unfold import KIND_CUT, KIND_FOLD, edge_key

PAGE_SIZES_MM = {
    "A5": (148.0, 210.0),
    "A4": (210.0, 297.0),
    "A3": (297.0, 420.0),
    "LETTER": (215.9, 279.4),
    "LEGAL": (215.9, 355.6),
    "TABLOID": (279.4, 431.8),
}


class Tab:
    __slots__ = ("island", "face", "corner", "number", "points", "squashed", "outer",
                 "partner", "weights", "angle")

    def __init__(self, island, face, corner, number, points, squashed=False):
        self.island = island
        self.face = face
        self.corner = corner
        self.number = number
        self.points = points  # island-local mm, before placement
        self.squashed = squashed  # could not avoid an overlap
        self.outer = False  # glued on the outside: printed with the covered art
        self.partner = None  # (face, corner) the tab glues onto
        self.weights = None  # per tab point: [(partner corner, weight), ...]
        self.angle = 0.0  # fold angle of the hinge (+ mountain, - valley)


class Placement:
    __slots__ = ("page", "transform", "width", "height")

    def __init__(self, page, transform, width, height):
        self.page = page
        self.transform = transform  # Rigid2D: island-local mm -> page mm
        self.width = width
        self.height = height


class Layout:
    def __init__(self):
        self.scale = 1.0  # paper mm per model unit
        self.page_w = 210.0
        self.page_h = 297.0
        self.margin = 10.0
        self.page_count = 0
        self.placements = []  # per island
        self.tabs = []
        self.fits = True
        self.messages = []

    # Convenience accessors -------------------------------------------------
    def face_points(self, res, island, face):
        """Face polygon in page millimetres."""
        t = self.placements[island.index].transform
        s = self.scale
        return [t.apply((p[0] * s, p[1] * s)) for p in island.faces[face]]

    def tab_points(self, tab):
        t = self.placements[tab.island].transform
        return [t.apply(p) for p in tab.points]

    def model_height_mm(self, res):
        if not res.verts:
            return 0.0
        zs = [v[2] for v in res.verts]
        return (max(zs) - min(zs)) * self.scale


def _island_mm(island, scale):
    return {f: [(p[0] * scale, p[1] * scale) for p in pts] for f, pts in island.faces.items()}


def _make_tab(a, b, height, angle_a, angle_b=None):
    """Trapezoid on the outside (right) of a->b. The sides lean in by
    angle_a at a and angle_b at b (smaller angle = steeper inset)."""
    if angle_b is None:
        angle_b = angle_a
    d = g.sub2(b, a)
    length = g.len2(d)
    if length < 1e-9:
        return None
    dn = g.mul2(d, 1.0 / length)
    out = (dn[1], -dn[0])  # right of a->b = outside of a CCW face
    cot_a = 1.0 / math.tan(angle_a)
    cot_b = 1.0 / math.tan(angle_b)
    if height * (cot_a + cot_b) > length * 0.9:
        height = length * 0.9 / (cot_a + cot_b)
    return [
        a,
        g.add2(g.add2(a, g.mul2(dn, height * cot_a)), g.mul2(out, height)),
        g.add2(g.sub2(b, g.mul2(dn, height * cot_b)), g.mul2(out, height)),
        b,
    ]


def _corner_map(res, island, mm_faces):
    """vertex id -> [(position, [neighbour positions])] for each face corner."""
    cmap = {}
    for f, pts in mm_faces.items():
        loop = res.faces[f].loop
        count = len(loop)
        for k, vid in enumerate(loop):
            cmap.setdefault(vid, []).append((pts[k], (pts[k - 1], pts[(k + 1) % count])))
    return cmap


def _free_angle(cmap, vid, pos, ref, clockwise, tol):
    """Angle swept from direction `ref` (clockwise or counter-clockwise)
    before hitting another piece's edge at this corner."""
    best = math.pi
    for p, nbrs in cmap.get(vid, ()):
        if abs(p[0] - pos[0]) > tol or abs(p[1] - pos[1]) > tol:
            continue
        for q in nbrs:
            d = g.sub2(q, pos)
            if g.len2(d) < tol:
                continue
            ang = math.atan2(g.cross2(ref, d), g.dot2(ref, d))
            phi = (-ang if clockwise else ang) % (2 * math.pi)
            if 1e-6 < phi < 2 * math.pi - 1e-6:
                best = min(best, phi)
    return best


def _overlaps_any(poly, others, eps):
    bb = g.bbox(poly)
    for other, obb in others:
        if g.bbox_overlap(bb, obb, 1e-6) and g.polygons_overlap(poly, other, eps):
            return True
    return False


def _clip_halfplane(poly, p, d, offset):
    """Keep the part of `poly` left of the line through p with direction d,
    moved `offset` inwards (Sutherland-Hodgman, one plane)."""
    length = g.len2(d)
    if length < 1e-12:
        return poly
    n = (-d[1] / length, d[0] / length)  # left normal

    def dist(q):
        return (q[0] - p[0]) * n[0] + (q[1] - p[1]) * n[1] - offset

    out = []
    count = len(poly)
    for i in range(count):
        cur, nxt = poly[i], poly[(i + 1) % count]
        dc, dn = dist(cur), dist(nxt)
        if dc >= -1e-9:
            out.append(cur)
        if (dc >= -1e-9) != (dn >= -1e-9):
            t = dc / (dc - dn)
            out.append((cur[0] + (nxt[0] - cur[0]) * t, cur[1] + (nxt[1] - cur[1]) * t))
    return out


def _partner_region(res, scale, other_use, a, b):
    """The face a tab glues onto, laid flat across edge a->b (where the tab
    sits). Returns its corner list in the tab's coordinates and the index of
    the shared edge."""
    fo, ko = other_use
    face = res.faces[fo]
    pts = [(x * scale, y * scale) for x, y in face.local]
    n = len(pts)
    # The partner traverses the shared edge j->i at corner ko.
    pb_j, pb_i = pts[ko], pts[(ko + 1) % n]
    t = g.Rigid2D.align_segment(pb_i, pb_j, a, b)
    return [t.apply(q) for q in pts], ko


def _fit_tab(trap, region, ko, margin):
    """Trim a tab so it lies inside the face it glues onto. Edges meeting
    the hinge clip exactly (the tab may run along them); the face's far
    edges are kept `margin` away so the tab never shows past them."""
    poly = list(trap)
    n = len(region)
    for k in range(n):
        if k == ko:
            continue  # the hinge itself
        p, q = region[k], region[(k + 1) % n]
        adjacent = k == (ko - 1) % n or k == (ko + 1) % n
        poly = _clip_halfplane(poly, p, g.sub2(q, p), 0.0 if adjacent else margin)
        if len(poly) < 3:
            return None
    # Drop repeated corners (a corner exactly on a clip line appears twice).
    clean = []
    for pt in poly:
        if not clean or g.len2(g.sub2(pt, clean[-1])) > 1e-7:
            clean.append(pt)
    while len(clean) > 1 and g.len2(g.sub2(clean[0], clean[-1])) <= 1e-7:
        clean.pop()
    return clean if len(clean) >= 3 else None


def _starts_at(poly, a, b):
    """Rotate a tab outline so it runs a -> ... -> b (hinge last)."""
    if not poly:
        return poly
    i = min(range(len(poly)), key=lambda k: g.len2(g.sub2(poly[k], a)))
    poly = poly[i:] + poly[:i]
    if g.len2(g.sub2(poly[-1], b)) > g.len2(g.sub2(poly[1], b)):
        poly = [poly[0]] + poly[1:][::-1]
    # Snap onto the hinge ends (slightly non-flat faces leave a few microns).
    snap = 0.05 * g.len2(g.sub2(b, a))
    if g.len2(g.sub2(poly[0], a)) < snap:
        poly[0] = a
    if g.len2(g.sub2(poly[-1], b)) < snap:
        poly[-1] = b
    return poly


def _glue_chains(res, keys, island_of_face):
    """Group glued edges into seams: runs of cut edges joining the same two
    pieces, connected end to end (split at junctions where more than two
    meet), with a consistent direction. Returns a
    list of chains, each a list of (key, (p, q)) in walking order."""
    groups = {}
    for key in keys:
        (fa, _), (fb, _) = res.edges[key].uses
        pair = tuple(sorted((island_of_face[fa], island_of_face[fb])))
        groups.setdefault(pair, []).append(key)
    chains = []
    for pair_keys in groups.values():
        at_vertex = {}
        for key in pair_keys:
            for v in key:
                at_vertex.setdefault(v, []).append(key)
        seen = set()
        for start in pair_keys:
            if start in seen:
                continue
            chain = []
            seen.add(start)
            stack = [(start, start)]  # (key, oriented p->q)
            while stack:
                key, (p, q) = stack.pop()
                chain.append((key, (p, q)))
                for v, want_start in ((q, True), (p, False)):
                    if len(at_vertex.get(v, ())) != 2:
                        continue  # a junction: each branch is its own seam
                    for nk in at_vertex.get(v, ()):
                        if nk in seen:
                            continue
                        seen.add(nk)
                        other = nk[0] if nk[1] == v else nk[1]
                        # Continue the walk: leaving v forwards, or arriving at v.
                        stack.append((nk, (v, other) if want_start else (other, v)))
            chains.append(chain)
    return chains


def partner_weights(region, points):
    """Barycentric weights of each tab point over the partner face's corners
    (region = the partner face laid flat across the hinge). Lets anything
    stored per corner (UVs, colours, 3D position) be read for the tab."""
    tris = g.triangulate_2d(region)
    out = []
    for p in points:
        best = None
        for a, b, c in tris:
            pa, pb, pc = region[a], region[b], region[c]
            det = (pb[1] - pc[1]) * (pa[0] - pc[0]) + (pc[0] - pb[0]) * (pa[1] - pc[1])
            if abs(det) < 1e-12:
                continue
            wa = ((pb[1] - pc[1]) * (p[0] - pc[0]) + (pc[0] - pb[0]) * (p[1] - pc[1])) / det
            wb = ((pc[1] - pa[1]) * (p[0] - pc[0]) + (pa[0] - pc[0]) * (p[1] - pc[1])) / det
            wc = 1.0 - wa - wb
            worst = min(wa, wb, wc)
            if best is None or worst > best[0]:
                best = (worst, [(a, wa), (b, wb), (c, wc)])
        out.append(best[1] if best else [(0, 1.0)])
    return out


def build_tabs(res, scale, tab_height, tab_angle_deg, flips=frozenset(), outer=frozenset()):
    """One tab per glued edge pair.

    * Tabs along one seam all go on the same piece (easier to assemble),
      switching sides only for an edge where that side has no room.
    * Each tab is trimmed to fit inside the face it glues onto, so it never
      sticks out past that face.
    * Tab sides lean to fit the free space at the corners; tabs shorten
      rather than run into neighbouring parts.
    `flips`: edge keys whose tab side the user swapped.
    `outer`: edge keys whose tab glues on the outside of the partner face."""
    angle = math.radians(max(15.0, min(89.0, tab_angle_deg)))
    eps = 1e-9
    margin = min(0.5, 0.1 * tab_height)
    min_area = 0.2
    cmaps = {}
    island_of_face = {}
    mm = {}
    obstacles = {}
    for isl in res.islands:
        mm[isl.index] = _island_mm(isl, scale)
        obstacles[isl.index] = [(pts, g.bbox(pts)) for pts in mm[isl.index].values()]
        cmaps[isl.index] = _corner_map(res, isl, mm[isl.index])
        for f in isl.faces:
            island_of_face[f] = isl.index

    # Glued edges in a stable order (piece by piece, face by face).
    keys = []
    seen = set()
    for isl in res.islands:
        for f in isl.order:
            face = res.faces[f]
            count = len(face.loop)
            for k in range(count):
                key = edge_key(face.loop[k], face.loop[(k + 1) % count])
                info = res.edges[key]
                if info.kind == KIND_CUT and len(info.uses) == 2 and key not in seen:
                    seen.add(key)
                    keys.append(key)

    cache = {}

    def candidate(use, other, shrink):
        """Tab polygon for gluing edge on `use`'s side, or None."""
        ck = (use, shrink)
        if ck in cache:
            return cache[ck]
        fs, ks = use
        il = island_of_face[fs]
        pts = mm[il][fs]
        loop = res.faces[fs].loop
        a, b = pts[ks], pts[(ks + 1) % len(pts)]
        va, vb = loop[ks], loop[(ks + 1) % len(loop)]
        tol = max(g.len2(g.sub2(b, a)) * 1e-6, 1e-9)
        free_a = _free_angle(cmaps[il], va, a, g.sub2(b, a), True, tol)
        free_b = _free_angle(cmaps[il], vb, b, g.sub2(a, b), False, tol)
        ang_a = max(math.radians(3.0), min(angle, free_a * 0.8))
        ang_b = max(math.radians(3.0), min(angle, free_b * 0.8))
        poly = _make_tab(a, b, tab_height * shrink, ang_a, ang_b)
        if poly is not None:
            region, ko = _partner_region(res, scale, other, a, b)
            poly = _fit_tab(poly, region, ko, margin)
            if poly is not None and abs(g.polygon_area(poly)) < min_area:
                poly = None
            if poly is not None:
                poly = _starts_at(poly, a, b)
        cache[ck] = (il, poly)
        return cache[ck]

    def fits(use, other, shrink, placed_obstacles):
        il, poly = candidate(use, other, shrink)
        if poly is None or _overlaps_any(poly, placed_obstacles[il], eps):
            return None
        return il, poly

    def side_uses(key, p, q):
        """(left use, right use) for edge walked p->q: the face whose own
        winding runs p->q is on the left."""
        u0, u1 = res.edges[key].uses
        f0, k0 = u0
        loop = res.faces[f0].loop
        runs_pq = loop[k0] == p and loop[(k0 + 1) % len(loop)] == q
        return (u0, u1) if runs_pq else (u1, u0)

    tabs = []
    for chain in _glue_chains(res, keys, island_of_face):
        # Pick the side where more full-size tabs fit.
        scores = []
        for side in (0, 1):
            score = 0.0
            for key, (p, q) in chain:
                uses = side_uses(key, p, q)
                use, other = uses[side], uses[1 - side]
                ok = fits(use, other, 1.0, obstacles)
                if ok:
                    score += 1.0 + 1e-3 * abs(g.polygon_area(ok[1]))
            scores.append(score)
        side = 0 if scores[0] >= scores[1] else 1
        for key, (p, q) in chain:
            uses = side_uses(key, p, q)
            pref, alt = uses[side], uses[1 - side]
            if key in flips:
                pref, alt = alt, pref
            number = res.edges[key].number
            placed = None
            plan = [(pref, alt, s) for s in (1.0, 0.75, 0.5)]
            plan += [(alt, pref, s) for s in (1.0, 0.75, 0.5)]
            plan += [(pref, alt, s) for s in (0.35, 0.25)]
            plan += [(alt, pref, s) for s in (0.35, 0.25)]
            for use, other, shrink in plan:
                ok = fits(use, other, shrink, obstacles)
                if ok:
                    il, poly = ok
                    placed = Tab(il, use[0], use[1], number, poly)
                    break
            if placed is None:
                for use, other in ((pref, alt), (alt, pref)):
                    il, poly = candidate(use, other, 0.25)
                    if poly is not None:
                        placed = Tab(il, use[0], use[1], number, poly, squashed=True)
                        break
            if placed is None:
                continue  # no room at all (tiny edge): leave it untabbed
            # Remember the face it glues onto and how each corner maps onto
            # it (outside tabs print that face's art; rigs fold with it).
            fs, ks = placed.face, placed.corner
            other = [u for u in res.edges[key].uses if u != (fs, ks)][0]
            pts = mm[placed.island][fs]
            region, _ = _partner_region(res, scale, other, pts[ks], pts[(ks + 1) % len(pts)])
            placed.partner = other
            placed.weights = partner_weights(region, placed.points)
            placed.outer = key in outer
            placed.angle = res.edges[key].angle
            obstacles[placed.island].append((placed.points, g.bbox(placed.points)))
            tabs.append(placed)
    tabs.sort(key=lambda t: t.number)
    return tabs


def _island_extent(points):
    """Best rotation and resulting (w, h, transform-to-origin)."""
    angle = g.min_area_rect_angle(points)
    rot = g.Rigid2D(angle)
    rotated = [rot.apply(p) for p in points]
    b = g.bbox(rotated)
    return angle, b


class _Shelf:
    __slots__ = ("y", "h", "x")

    def __init__(self, y, h):
        self.y, self.h, self.x = y, h, 0.0


def _pack(sizes, usable_w, usable_h, spacing):
    """Shelf packing. sizes: list of (w, h). Returns list of
    (page, x, y, rotated) or None if something can never fit."""
    order = sorted(range(len(sizes)), key=lambda i: -max(sizes[i]))
    pages = []  # each page: list of shelves
    result = [None] * len(sizes)
    for i in order:
        w, h = sizes[i]
        options = []
        # Prefer lying flat (wide), fall back to rotated.
        if w <= usable_w and h <= usable_h:
            options.append((w, h, False))
        if h <= usable_w and w <= usable_h:
            options.append((h, w, True))
        if not options:
            return None
        options.sort(key=lambda o: o[1])  # lower shelves first
        placed = False
        for pi, shelves in enumerate(pages):
            for shelf in shelves:
                for ow, oh, rot in options:
                    if oh <= shelf.h + 1e-9 and shelf.x + ow <= usable_w + 1e-9:
                        result[i] = (pi, shelf.x, shelf.y, rot)
                        shelf.x += ow + spacing
                        placed = True
                        break
                if placed:
                    break
            if placed:
                break
            top = shelves[-1].y + shelves[-1].h + spacing if shelves else 0.0
            for ow, oh, rot in options:
                if top + oh <= usable_h + 1e-9:
                    shelf = _Shelf(top, oh)
                    shelves.append(shelf)
                    result[i] = (pi, 0.0, top, rot)
                    shelf.x = ow + spacing
                    placed = True
                    break
            if placed:
                break
        if not placed:
            ow, oh, rot = options[0]
            shelf = _Shelf(0.0, oh)
            shelf.x = ow + spacing
            pages.append([shelf])
            result[i] = (len(pages) - 1, 0.0, 0.0, rot)
    return result, len(pages)


def _approx_pages(res, scale, usable_w, usable_h, spacing, pad, unit_sizes):
    sizes = [(w * scale + 2 * pad, h * scale + 2 * pad) for w, h in unit_sizes]
    packed = _pack(sizes, usable_w, usable_h, spacing)
    return None if packed is None else packed[1]


def model_size(res):
    if not res.verts:
        return 1.0
    xs, ys, zs = zip(*res.verts)
    return max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs), 1e-9)


def compute_layout(
    res,
    page_w=210.0,
    page_h=297.0,
    margin=10.0,
    spacing=3.0,
    scale_mode="FIT",
    scale_value=1.0,
    max_pages=1,
    tab_height=5.0,
    tab_angle=45.0,
    tab_flips=frozenset(),
    tab_outer=frozenset(),
    use_tabs=True,
):
    """use_tabs: False for board joined edge to edge (mitres): no glue tabs.
    scale_mode:
    'FIT'    largest scale that fits on `max_pages` pages
    'HEIGHT' model's largest dimension becomes `scale_value` mm
    'RATIO'  1 model unit = `scale_value` mm
    """
    lay = Layout()
    lay.page_w, lay.page_h, lay.margin = page_w, page_h, margin
    usable_w = page_w - 2 * margin
    usable_h = page_h - 2 * margin
    if not res.islands or usable_w <= 0 or usable_h <= 0:
        lay.fits = False
        lay.messages.append("Nothing to lay out (empty mesh or margins too large)")
        return lay

    size = model_size(res)
    if scale_mode == "RATIO":
        scale = scale_value
    elif scale_mode == "HEIGHT":
        scale = scale_value / size
    else:
        max_pages = max(1, int(max_pages))
        # Upper bound: total island area cannot exceed the paper area.
        area = sum(
            abs(res.faces[f].area) for isl in res.islands for f in isl.faces
        )
        hi = math.sqrt(max_pages * usable_w * usable_h / max(area, 1e-12))
        lo = hi * 1e-3
        unit_sizes = []  # min-area extents at scale 1 (rotation is scale-free)
        for isl in res.islands:
            _, b = _island_extent(res.island_outline_points(isl))
            unit_sizes.append((b[2] - b[0], b[3] - b[1]))
        args = (usable_w, usable_h, spacing, tab_height if use_tabs else 0.0, unit_sizes)
        if _approx_pages(res, lo, *args) is None:
            lay.fits = False
            lay.messages.append("Tabs or spacing are too large for this page size")
            return lay
        for _ in range(28):
            mid = math.sqrt(lo * hi)
            pages = _approx_pages(res, mid, *args)
            if pages is not None and pages <= max_pages:
                lo = mid
            else:
                hi = mid
        scale = lo
    lay.scale = scale

    for attempt in range(12):
        tabs = (build_tabs(res, scale, tab_height, tab_angle, tab_flips, tab_outer)
                if use_tabs else [])
        tab_pts = {}
        for t in tabs:
            tab_pts.setdefault(t.island, []).extend(t.points)
        extents = []
        sizes = []
        for isl in res.islands:
            pts = [(p[0] * scale, p[1] * scale) for p in res.island_outline_points(isl)]
            pts += tab_pts.get(isl.index, [])
            angle, b = _island_extent(pts)
            extents.append((angle, b))
            sizes.append((b[2] - b[0], b[3] - b[1]))
        packed = _pack(sizes, usable_w, usable_h, spacing)
        if packed is not None and (scale_mode != "FIT" or packed[1] <= max_pages):
            break
        if scale_mode != "FIT":
            break
        scale *= 0.97
        lay.scale = scale

    if packed is None:
        lay.fits = False
        big = max(sizes, key=lambda s: s[0] * s[1])
        lay.messages.append(
            f"A piece is {big[0]:.0f} x {big[1]:.0f} mm and will not fit on the "
            f"{usable_w:.0f} x {usable_h:.0f} mm printable area; reduce the scale "
            "or add seams"
        )
        # Still produce a layout (one island per page) so the user sees it.
        places = [(i, 0.0, 0.0, False) for i in range(len(sizes))]
        page_count = len(sizes)
    else:
        places, page_count = packed

    lay.tabs = tabs
    lay.page_count = page_count
    lay.placements = []
    for isl, (angle, b), (page, x, y, rotated), (w, h) in zip(
        res.islands, extents, places, sizes
    ):
        # island-local mm -> rotate to min-area -> move bbox corner to origin
        # -> optional 90 degree turn -> slot position on the page.
        total = angle + (math.pi / 2 if rotated else 0.0)
        t = g.Rigid2D(total)
        if rotated:
            # After +90 deg the old bbox (x0..x1, y0..y1) maps to
            # (-y1..-y0, x0..x1).
            ox, oy = -b[3], b[0]
            ww, hh = h, w
        else:
            ox, oy = b[0], b[1]
            ww, hh = w, h
        t.tx = margin + x - ox
        t.ty = margin + y - oy
        lay.placements.append(Placement(page, t, ww, hh))

    if scale_mode == "FIT" and page_count > max_pages:
        lay.fits = False
    squashed = sum(1 for t in tabs if t.squashed)
    if squashed:
        lay.messages.append(f"{squashed} tab(s) touch a neighbouring piece (red on the layout)")
    return lay


def fold_kind(info):
    """'mountain', 'valley' or 'flat' for a fold edge, seen from the printed side."""
    if info.kind != KIND_FOLD:
        return None
    if abs(info.angle) < math.radians(0.5):
        return "flat"
    return "mountain" if info.angle > 0 else "valley"
