# SPDX-License-Identifier: GPL-3.0-or-later
"""Assembly page: where every piece goes on the finished model.

Four views of the model (front and back three-quarter views, top and
bottom), each piece filled in its own colour and lettered where it shows,
seams drawn darker than folds, and a key saying which page each piece is
printed on. The same letters are printed on the pieces (piece_labels), so
a piece on the sheet can be found on the model and back.

Pure Python: faces are projected orthographically and drawn back to front.
"""

import colorsys
import math
from xml.sax.saxutils import escape

from . import geom as g
from .unfold import KIND_CUT, edge_key

MARK = "<!-- papercraft:assembly -->"

VIEWS = [
    ("Front", (-0.9, -1.2, 0.8), (0.0, 0.0, 1.0)),
    ("Back", (0.9, 1.2, 0.8), (0.0, 0.0, 1.0)),
    ("Top", (0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),
    ("Bottom", (0.0, 0.0, -1.0), (0.0, -1.0, 0.0)),
]


def letter(n):
    """0 -> A, 25 -> Z, 26 -> AA ..."""
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def piece_labels(res, lay):
    """{island index: letter}, in reading order: page, then top to bottom
    (5 mm rows), then left to right."""
    keys = []
    for isl in res.islands:
        pts = [p for f in isl.faces for p in lay.face_points(res, isl, f)]
        if not pts:
            continue
        top = max(p[1] for p in pts)
        left = min(p[0] for p in pts)
        keys.append(((lay.placements[isl.index].page, -round(top / 5.0), left), isl.index))
    keys.sort()
    return {idx: letter(n) for n, (_, idx) in enumerate(keys)}


def piece_colours(count):
    """Distinct soft colours (golden-angle hues)."""
    out = []
    for n in range(count):
        h = (n * 0.381966) % 1.0
        r, g_, b = colorsys.hls_to_rgb(h, 0.78, 0.55)
        out.append((r, g_, b))
    return out


def _hex(rgb):
    return "#" + "".join(f"{max(0, min(255, round(c * 255))):02x}" for c in rgb)


def _fmt(v):
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def _norm(v):
    ln = math.sqrt(sum(c * c for c in v)) or 1.0
    return tuple(c / ln for c in v)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _basis(eye, up_hint):
    e = _norm(eye)
    f = tuple(-c for c in e)  # looking direction
    right = _norm(_cross(f, up_hint))
    up = _cross(right, f)
    return e, f, right, up


def _inside(poly, p):
    hit = False
    n = len(poly)
    for k in range(n):
        a, b = poly[k], poly[(k + 1) % n]
        if (a[1] > p[1]) != (b[1] > p[1]):
            x = a[0] + (p[1] - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
            if x > p[0]:
                hit = not hit
    return hit


def assembly_page(res, lay, title="Papercraft", labels=None, line_width=0.25):
    labels = labels or piece_labels(res, lay)
    island_of = {f: isl.index for isl in res.islands for f in isl.faces}
    order = sorted(labels, key=lambda i: labels[i])
    colour = dict(zip(order, piece_colours(len(order))))
    W, H = lay.page_w, lay.page_h
    m = max(lay.margin, 8.0)
    faces = [f for f in res.faces if f is not None and f.index in island_of]
    verts = res.verts
    light = _norm((-0.4, -0.6, 0.8))

    # Project every view; one scale for all, so sizes compare.
    projected = []
    for name, eye, up_hint in VIEWS:
        e, f_dir, right, up = _basis(eye, up_hint)
        items = []
        for face in faces:
            if _dot(face.normal, e) <= 1e-6:
                continue  # facing away
            pts = [(_dot(verts[v], right), -_dot(verts[v], up)) for v in face.loop]
            depth = sum(_dot(verts[v], f_dir) for v in face.loop) / len(face.loop)
            items.append((depth, face, pts))
        items.sort(key=lambda t: -t[0])  # far first
        projected.append((name, items))
    legend_rows = math.ceil(len(order) / 6) if order else 0
    legend_h = 8.0 + 5.0 * legend_rows
    top = m + 10.0
    cell_w = (W - 2 * m - 6.0) / 2
    cell_h = (H - top - m - legend_h - 6.0) / 2
    scale = None
    for _, items in projected:
        pts = [p for _, _, ps in items for p in ps]
        if not pts:
            continue
        w = max(p[0] for p in pts) - min(p[0] for p in pts)
        h = max(p[1] for p in pts) - min(p[1] for p in pts)
        s = min((cell_w - 6.0) / max(w, 1e-9), (cell_h - 10.0) / max(h, 1e-9))
        scale = s if scale is None else min(scale, s)
    scale = scale or 1.0

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        MARK,
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
        f'width="{_fmt(W)}mm" height="{_fmt(H)}mm" viewBox="0 0 {_fmt(W)} {_fmt(H)}">',
        f"<title>{escape(title)} assembly</title>",
        f'<text x="{_fmt(m)}" y="{_fmt(m + 4)}" style="font-family:sans-serif;font-size:6px;'
        f'fill:#222222">{escape(title)} · Assembly</text>',
        f'<text x="{_fmt(m)}" y="{_fmt(m + 9)}" style="font-family:sans-serif;font-size:3px;'
        f'fill:#666666">Each piece is lettered on its sheet. Glue numbers match across each '
        f'seam; darker lines are seams, lighter ones folds.</text>',
    ]
    for n, (name, items) in enumerate(projected):
        cx = m + (n % 2) * (cell_w + 6.0)
        cy = top + (n // 2) * (cell_h + 6.0)
        pts = [p for _, _, ps in items for p in ps] or [(0.0, 0.0)]
        x0 = (min(p[0] for p in pts) + max(p[0] for p in pts)) / 2
        y0 = (min(p[1] for p in pts) + max(p[1] for p in pts)) / 2
        ox, oy = cx + cell_w / 2 - x0 * scale, cy + 5.0 + (cell_h - 5.0) / 2 - y0 * scale

        def P(p):
            return (ox + p[0] * scale, oy + p[1] * scale)

        parts.append(f'<g id="view_{name.lower()}" inkscape:groupmode="layer" '
                     f'inkscape:label="{name}">')
        parts.append(f'<text x="{_fmt(cx)}" y="{_fmt(cy + 3)}" style="font-family:sans-serif;'
                     f'font-size:3.5px;fill:#444444">{name}</text>')
        drawn = []
        for depth, face, ps in items:
            q = [P(p) for p in ps]
            isl = island_of[face.index]
            base = colour.get(isl, (0.85, 0.85, 0.85))
            shade = 0.72 + 0.28 * max(0.0, _dot(face.normal, light))
            fill = _hex(tuple(c * shade for c in base))
            edge = _hex(tuple(c * shade * 0.8 for c in base))
            pts_s = " ".join(f"{_fmt(x)},{_fmt(y)}" for x, y in q)
            parts.append(f'<polygon points="{pts_s}" style="fill:{fill};stroke:{edge};'
                         f'stroke-width:{_fmt(line_width * 0.5)};stroke-linejoin:round"/>')
            k_n = len(face.loop)
            for k in range(k_n):
                info = res.edges.get(edge_key(face.loop[k], face.loop[(k + 1) % k_n]))
                if info is not None and (info.kind == KIND_CUT or len(info.uses) < 2):
                    a, b = q[k], q[(k + 1) % k_n]
                    parts.append(f'<path d="M {_fmt(a[0])},{_fmt(a[1])} L {_fmt(b[0])},'
                                 f'{_fmt(b[1])}" style="fill:none;stroke:#333333;'
                                 f'stroke-width:{_fmt(line_width * 1.4)};'
                                 'stroke-linecap:round"/>')
            drawn.append((isl, q))
        # Letters: on each piece's biggest face that isn't hidden behind
        # something nearer.
        best = {}
        for i, (isl, q) in enumerate(drawn):
            area = abs(g.polygon_area(q))
            if area < 0.8:
                continue
            c = g.polygon_centroid(q)
            if not _inside(q, c):
                continue
            if any(_inside(q2, c) for _, q2 in drawn[i + 1:]):
                continue
            if area > best.get(isl, (0.0, None))[0]:
                best[isl] = (area, c)
        for isl, (area, c) in best.items():
            small = area < 4.0  # a small piece: a smaller badge
            r, size = (1.6, 2.3) if small else (2.3, 3.2)
            badge = " ".join(
                f"{_fmt(c[0] + r * math.cos(a))},{_fmt(c[1] + r * math.sin(a))}"
                for a in (k * math.pi / 4 + math.pi / 8 for k in range(8)))
            parts.append(f'<polygon points="{badge}" style="fill:#ffffff;fill-opacity:0.85;'
                         'stroke:#333333;stroke-width:0.2"/>')
            parts.append(f'<text x="{_fmt(c[0])}" y="{_fmt(c[1] + size * 0.35)}" '
                         f'text-anchor="middle" style="font-family:sans-serif;'
                         f'font-size:{_fmt(size)}px;fill:#111111">{labels[isl]}</text>')
        parts.append("</g>")

    # Key: piece letter, colour, page.
    ky = H - m - legend_h + 4.0
    parts.append('<g id="key" inkscape:groupmode="layer" inkscape:label="key">')
    parts.append(f'<text x="{_fmt(m)}" y="{_fmt(ky)}" style="font-family:sans-serif;'
                 'font-size:3.2px;fill:#444444">Pieces</text>')
    col_w = (W - 2 * m) / 6
    for n, isl in enumerate(order):
        x = m + (n % 6) * col_w
        y = ky + 3.0 + (n // 6) * 5.0
        sw = (f"{_fmt(x)},{_fmt(y)} {_fmt(x + 3.5)},{_fmt(y)} "
              f"{_fmt(x + 3.5)},{_fmt(y + 3.5)} {_fmt(x)},{_fmt(y + 3.5)}")
        parts.append(f'<polygon points="{sw}" style="fill:{_hex(colour[isl])};stroke:#555555;'
                     'stroke-width:0.15"/>')
        page = lay.placements[isl].page + 1
        parts.append(f'<text x="{_fmt(x + 5)}" y="{_fmt(y + 2.9)}" style="font-family:'
                     f'sans-serif;font-size:3px;fill:#222222">{labels[isl]} · page {page}</text>')
    parts.append("</g>")
    parts.append("</svg>")
    return "\n".join(parts)
