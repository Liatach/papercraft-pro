# SPDX-License-Identifier: GPL-3.0-or-later
"""SVG pages for printing or for cutting machines (Cricut, laser, plotter).

Layers (Inkscape layers, so they can be toggled for print vs cut):
  faces     face fill colours (print only)
  tabs      glue tab fill (print only)
  cut       solid outline to cut
  mountain  dash-dot folds (fold away from you, printed side up)
  valley    dashed folds (fold towards you)
  numbers   matching glue-edge numbers
"""

import math
from xml.sax.saxutils import escape

from . import geom as g
from .layout import fold_kind
from .unfold import KIND_CUT, KIND_FOLD, edge_key

STYLE = {
    "cut": "fill:none;stroke:#000000;stroke-width:0.25;stroke-linejoin:round",
    "mountain": "fill:none;stroke:#c0392b;stroke-width:0.25;stroke-dasharray:3,1,0.5,1",
    "valley": "fill:none;stroke:#2471a3;stroke-width:0.25;stroke-dasharray:1.5,1",
    "tab": "fill:#e9e6df;stroke:none",
}


def _hex(rgb):
    r, gg, b = (max(0, min(255, int(round(c * 255)))) for c in rgb[:3])
    return f"#{r:02x}{gg:02x}{b:02x}"


def _fmt(v):
    return f"{v:.3f}".rstrip("0").rstrip(".")


def export_pages(
    res,
    lay,
    colors=None,
    fill_faces=True,
    numbers=True,
    title="Papercraft",
):
    """Return a list of SVG documents (strings), one per page.

    colors: optional list of (r, g, b) per material index (0..1 floats).
    """
    H = lay.page_h

    def pt(p):
        return f"{_fmt(p[0])},{_fmt(H - p[1])}"

    def line(a, b):
        return f'<path d="M {pt(a)} L {pt(b)}"/>'

    pages = [
        {"faces": [], "tabs": [], "cut": [], "mountain": [], "valley": [], "numbers": []}
        for _ in range(max(lay.page_count, 1))
    ]

    tab_on = {}  # (face, corner) -> Tab
    for tab in lay.tabs:
        tab_on[(tab.face, tab.corner)] = tab

    for isl in res.islands:
        place = lay.placements[isl.index]
        page = pages[place.page]
        folds_done = set()
        for f in isl.order:
            face = res.faces[f]
            pts = lay.face_points(res, isl, f)
            count = len(pts)
            if fill_faces:
                col = "#ffffff"
                if colors and face.material < len(colors) and colors[face.material]:
                    col = _hex(colors[face.material])
                page["faces"].append(
                    f'<polygon points="{" ".join(pt(p) for p in pts)}" '
                    f'style="fill:{col};stroke:{col};stroke-width:0.05"/>'
                )
            for k in range(count):
                a, b = pts[k], pts[(k + 1) % count]
                key = edge_key(face.loop[k], face.loop[(k + 1) % count])
                info = res.edges[key]
                if info.kind == KIND_FOLD:
                    if key in folds_done:
                        continue
                    folds_done.add(key)
                    kind = fold_kind(info)
                    if kind in ("mountain", "valley"):
                        page[kind].append(line(a, b))
                    continue
                tab = tab_on.get((f, k))
                if tab is not None:
                    tp = lay.tab_points(tab)
                    page["tabs"].append(
                        f'<polygon points="{" ".join(pt(p) for p in tp)}"/>'
                    )
                    page["cut"].append(
                        f'<path d="M {pt(tp[0])} L {pt(tp[1])} L {pt(tp[2])} L {pt(tp[3])}"/>'
                    )
                    page["mountain"].append(line(a, b))  # tab folds behind
                else:
                    page["cut"].append(line(a, b))
                if numbers and info.kind == KIND_CUT and info.number:
                    page["numbers"].append(_number(a, b, info.number, H))

    docs = []
    total = len(pages)
    for n, layers in enumerate(pages, 1):
        footer = (
            f"{escape(title)} · page {n}/{total} · 1 model unit = "
            f"{lay.scale:.2f} mm · model height {lay.model_height_mm(res):.0f} mm"
        )
        parts = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
            f'width="{_fmt(lay.page_w)}mm" height="{_fmt(lay.page_h)}mm" '
            f'viewBox="0 0 {_fmt(lay.page_w)} {_fmt(lay.page_h)}">',
            f"<title>{escape(title)} page {n}</title>",
        ]
        groups = [
            ("faces", ""),
            ("tabs", STYLE["tab"]),
            ("mountain", STYLE["mountain"]),
            ("valley", STYLE["valley"]),
            ("cut", STYLE["cut"]),
            ("numbers", "font-family:sans-serif;fill:#333333"),
        ]
        for name, style in groups:
            attr = f' style="{style}"' if style else ""
            parts.append(
                f'<g id="{name}" inkscape:groupmode="layer" inkscape:label="{name}"{attr}>'
            )
            parts.extend(layers[name])
            parts.append("</g>")
        parts.append(
            f'<text x="{_fmt(lay.margin)}" y="{_fmt(H - lay.margin / 3)}" '
            f'style="font-family:sans-serif;font-size:2.5px;fill:#888888">{footer}</text>'
        )
        parts.append("</svg>")
        docs.append("\n".join(parts))
    return docs


def _number(a, b, number, page_h):
    d = g.sub2(b, a)
    length = g.len2(d)
    if length < 1e-6:
        return ""
    size = max(1.4, min(3.0, length * 0.3))
    dn = g.mul2(d, 1.0 / length)
    inside = (-dn[1], dn[0])  # left of a->b is inside a CCW face
    mid = g.mul2(g.add2(a, b), 0.5)
    pos = g.add2(mid, g.mul2(inside, size * 0.35))
    angle = math.degrees(math.atan2(dn[1], dn[0]))
    x, y = pos[0], page_h - pos[1]
    return (
        f'<text x="{_fmt(x)}" y="{_fmt(y)}" text-anchor="middle" '
        f'style="font-size:{_fmt(size)}px" '
        f'transform="rotate({_fmt(-angle)} {_fmt(x)} {_fmt(y)})">{number}</text>'
    )
