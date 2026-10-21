# SPDX-License-Identifier: GPL-3.0-or-later
"""SVG pages for printing or for cutting machines (Cricut, laser, plotter).

Layers (Inkscape layers, so they can be toggled for print vs cut):
  faces     face colours or UV-mapped texture (print)
  tabs      glue tab fill (print)
  mountain  dash-dot folds (fold away from you, printed side up)
  valley    dashed folds (fold towards you)
  cut       outline to cut
  numbers   matching glue-edge numbers

Line styles:
  DEFAULT   black cuts, red mountain, blue valley
  MULTIPLY  grey lines multiplied over the print (darker version of the art)
  BURN      grey lines colour-burned into the print (richer, more saturated)
  NONE      lines kept in the file but hidden (print art only; show the
            layers again in Inkscape for a cutting machine)
"""

import math
from xml.sax.saxutils import escape

from . import geom as g
from .unfold import KIND_CUT, KIND_FOLD, edge_key

LINE_STYLES = ("DEFAULT", "MULTIPLY", "BURN", "NONE")


class Texture:
    """An image to print on faces. href is a data: URI (or a file path);
    width/height are the image's pixel size (UV 0..1 spans these)."""

    __slots__ = ("href", "width", "height")

    def __init__(self, href, width, height):
        self.href = href
        self.width = width
        self.height = height


def _hex(rgb):
    r, gg, b = (max(0, min(255, int(round(c * 255)))) for c in rgb[:3])
    return f"#{r:02x}{gg:02x}{b:02x}"


def _fmt(v):
    return f"{v:.3f}".rstrip("0").rstrip(".") or "0"


def _line_styles(style, darkness, width):
    w = _fmt(width)
    dash_m = f"{_fmt(width * 12)},{_fmt(width * 4)},{_fmt(width * 2)},{_fmt(width * 4)}"
    dash_v = f"{_fmt(width * 6)},{_fmt(width * 4)}"
    base = f"fill:none;stroke-width:{w};stroke-linejoin:round;stroke-linecap:round"
    if style in ("MULTIPLY", "BURN"):
        grey = _hex((1.0 - darkness,) * 3)
        blend = "multiply" if style == "MULTIPLY" else "color-burn"
        extra = f";stroke:{grey};mix-blend-mode:{blend}"
        return {
            "cut": base + extra,
            "mountain": base + extra + f";stroke-dasharray:{dash_m}",
            "valley": base + extra + f";stroke-dasharray:{dash_v}",
        }
    styles = {
        "cut": base + ";stroke:#000000",
        "mountain": base + f";stroke:#c0392b;stroke-dasharray:{dash_m}",
        "valley": base + f";stroke:#2471a3;stroke-dasharray:{dash_v}",
    }
    if style == "NONE":
        styles = {k: v + ";display:none" for k, v in styles.items()}
    return styles


def export_pages(
    res,
    lay,
    colors=None,
    fill_faces=True,
    numbers=True,
    title="Papercraft",
    textures=None,
    uvs=None,
    line_style="DEFAULT",
    line_darkness=0.5,
    line_width=0.25,
    fold_min_angle=1.0,
    bleed=0.2,
    tab_color=(0.914, 0.902, 0.875),
):
    """Return a list of SVG documents (strings), one per page.

    colors    per material index (r, g, b) floats, or None
    textures  per material index Texture, or None
    uvs       per *source* polygon list of (u, v) per corner
    fold_min_angle  folds flatter than this (degrees) are not drawn
    bleed     mm of colour/texture spill past edges, hides white slivers
    """
    H = lay.page_h
    min_fold = math.radians(max(0.0, fold_min_angle))
    lines = _line_styles(line_style, line_darkness, line_width)

    def svgp(p):
        return (p[0], H - p[1])

    def pt(p):
        return f"{_fmt(p[0])},{_fmt(H - p[1])}"

    def line(a, b):
        return f'<path d="M {pt(a)} L {pt(b)}"/>'

    page_count = max(lay.page_count, 1)
    pages = [
        {"defs": [], "faces": [], "tabs": [], "cut": [], "mountain": [], "valley": [],
         "numbers": [], "tex_used": set()}
        for _ in range(page_count)
    ]
    pattern_n = [0]

    tab_on = {(t.face, t.corner): t for t in lay.tabs}

    def face_fill(page, face, pts):
        """Append the face's print fill: texture triangles or flat colour."""
        tex = None
        if textures and face.material < len(textures):
            tex = textures[face.material]
        face_uv = None
        if tex is not None and uvs and face.source < len(uvs) and uvs[face.source]:
            src_uv = uvs[face.source]
            face_uv = [src_uv[c] for c in face.corners]
        if tex is not None and face_uv is not None:
            done = 0
            for a, b, c in g.triangulate_2d(pts):
                img = [
                    (face_uv[k][0] * tex.width, (1.0 - face_uv[k][1]) * tex.height)
                    for k in (a, b, c)
                ]
                dst = [svgp(pts[k]) for k in (a, b, c)]
                m = g.affine_from_triangles(img, dst)
                if m is None:
                    continue
                tid = f"tex{textures.index(tex)}"
                page["tex_used"].add(tid)
                pattern_n[0] += 1
                pid = f"p{pattern_n[0]}"
                page["defs"].append(
                    f'<pattern id="{pid}" patternUnits="userSpaceOnUse" '
                    f'width="{tex.width}" height="{tex.height}" '
                    f'patternTransform="matrix({" ".join(f"{v:.8g}" for v in m)})">'
                    f'<use xlink:href="#{tid}" href="#{tid}"/></pattern>'
                )
                grown = g.grow_triangle(dst, bleed)
                page["faces"].append(
                    f'<polygon points="{" ".join(f"{_fmt(x)},{_fmt(y)}" for x, y in grown)}" '
                    f'fill="url(#{pid})"/>'
                )
                done += 1
            if done:
                return
        col = "#ffffff"
        if colors and face.material < len(colors) and colors[face.material]:
            col = _hex(colors[face.material])
        page["faces"].append(
            f'<polygon points="{" ".join(pt(p) for p in pts)}" '
            f'style="fill:{col};stroke:{col};stroke-width:{_fmt(max(bleed * 2, 0.05))};'
            f'stroke-linejoin:round"/>'
        )

    for isl in res.islands:
        place = lay.placements[isl.index]
        page = pages[place.page]
        folds_done = set()
        for f in isl.order:
            face = res.faces[f]
            pts = lay.face_points(res, isl, f)
            count = len(pts)
            if fill_faces:
                face_fill(page, face, pts)
            for k in range(count):
                a, b = pts[k], pts[(k + 1) % count]
                key = edge_key(face.loop[k], face.loop[(k + 1) % count])
                info = res.edges[key]
                if info.kind == KIND_FOLD:
                    if key in folds_done:
                        continue
                    folds_done.add(key)
                    if abs(info.angle) >= min_fold and abs(info.angle) > 1e-9:
                        page["mountain" if info.angle > 0 else "valley"].append(line(a, b))
                    continue
                tab = tab_on.get((f, k))
                if tab is not None:
                    tp = lay.tab_points(tab)
                    page["tabs"].append(f'<polygon points="{" ".join(pt(p) for p in tp)}"/>')
                    page["cut"].append(
                        f'<path d="M {pt(tp[0])} L {pt(tp[1])} L {pt(tp[2])} L {pt(tp[3])}"/>'
                    )
                    page["mountain"].append(line(a, b))  # tab folds behind
                else:
                    page["cut"].append(line(a, b))
                if numbers and info.kind == KIND_CUT and info.number:
                    page["numbers"].append(_number(a, b, info.number, H))

    docs = []
    for n, layers in enumerate(pages, 1):
        footer = (
            f"{escape(title)} · page {n}/{page_count} · 1 model unit = "
            f"{lay.scale:.2f} mm · model height {lay.model_height_mm(res):.0f} mm"
        )
        parts = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
            f'width="{_fmt(lay.page_w)}mm" height="{_fmt(lay.page_h)}mm" '
            f'viewBox="0 0 {_fmt(lay.page_w)} {_fmt(lay.page_h)}">',
            f"<title>{escape(title)} page {n}</title>",
            "<defs>",
        ]
        for tid in sorted(layers["tex_used"]):
            tex = textures[int(tid[3:])]
            parts.append(
                f'<image id="{tid}" width="{tex.width}" height="{tex.height}" '
                f'preserveAspectRatio="none" xlink:href="{tex.href}" href="{tex.href}"/>'
            )
        parts.extend(layers["defs"])
        parts.append("</defs>")
        groups = [
            ("faces", ""),
            ("tabs", f"fill:{_hex(tab_color)};stroke:none"),
            ("mountain", lines["mountain"]),
            ("valley", lines["valley"]),
            ("cut", lines["cut"]),
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
