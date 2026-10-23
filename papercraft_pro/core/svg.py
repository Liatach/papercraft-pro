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
  CUSTOM    one colour of your choice at a chosen opacity (e.g. white at
            40% over dark art, black at 30% over light art)
  NONE      lines kept in the file but hidden (print art only; show the
            layers again in Inkscape for a cutting machine)

Numbers: FACES (inside both edges), CLEAN (on the tab, and on the scrap
just outside the other edge, so nothing prints on the art) or OFF.
"""

import math
from xml.sax.saxutils import escape

from . import geom as g
from .unfold import KIND_CUT, KIND_FOLD, edge_key

LINE_STYLES = ("DEFAULT", "CUSTOM", "NONE")
NUMBER_MODES = ("FACES", "CLEAN", "OFF")


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


def _line_styles(style, width, color=(0.0, 0.0, 0.0), opacity=1.0, dashed=True):
    w = _fmt(width)
    if dashed:
        dash_m = f"{_fmt(width * 12)},{_fmt(width * 4)},{_fmt(width * 2)},{_fmt(width * 4)}"
        dash_v = f"{_fmt(width * 6)},{_fmt(width * 4)}"
    else:  # cutting machines score each dash as a separate little line
        dash_m = dash_v = "none"
    base = f"fill:none;stroke-width:{w};stroke-linejoin:round;stroke-linecap:round"
    if style == "CUSTOM":
        extra = f";stroke:{_hex(color)};stroke-opacity:{_fmt(max(0.0, min(1.0, opacity)))}"
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
    line_color=(0.0, 0.0, 0.0),
    line_opacity=1.0,
    line_width=0.25,
    fold_min_angle=1.0,
    bleed=0.2,
    tab_color=(0.914, 0.902, 0.875),
    page_images=None,
    tab_texture=False,
    content="PRINT",
    dashed=True,
    tab_fill=True,
):
    """Return a list of SVG documents (strings), one per page.

    colors    per material index (r, g, b) floats, or None
    textures  per material index Texture, or None
    uvs       per material index: per *source* polygon list of (u, v) per
              corner (the UV map that material's image uses)
    page_images  per page Texture of a baked page (overrides face fills)
    content   PRINT (art and lines), or for cutting machines: CUT (outlines
              only), CREASE (fold lines only) or LINES (both; creases red so
              they import as their own colour layer). Lines-only files are
              plain solid strokes: no art, numbers or text.
              ART_CUT is Print with the art carried onto the tabs and only the
              cut lines drawn (no fold lines on the art).
    dashed    dash the fold lines (turn off for cutting machines)
    tab_texture  continue each face's art (baked page, image texture or
              flat colour) onto its tabs
    tab_fill  fill plain tabs grey (False: leave them paper white)
    fold_min_angle  folds flatter than this (degrees) are not drawn
    bleed     mm of colour/texture spill past edges, hides white slivers
    """
    H = lay.page_h
    min_fold = math.radians(max(0.0, fold_min_angle))
    lines = _line_styles(line_style, line_width, line_color, line_opacity, dashed)
    lines_only = content in ("CUT", "CREASE", "LINES")
    if content == "ART_CUT":
        tab_texture = True
    if lines_only:
        fill_faces = False
        page_images = None
        textures = None
        numbers = "OFF"
        tab_texture = False
        base = (f"fill:none;stroke-width:{_fmt(line_width)};stroke-linejoin:round;"
                "stroke-linecap:round")
        crease = "#ff0000" if content == "LINES" else "#000000"
        solid = _line_styles("DEFAULT", line_width, dashed=dashed)
        lines = {
            "cut": base + ";stroke:#000000",
            "mountain": base + f";stroke:{crease}" + solid["mountain"].split(";stroke:#c0392b")[1],
            "valley": base + f";stroke:{crease}" + solid["valley"].split(";stroke:#2471a3")[1],
        }
    if numbers is True:
        numbers = "FACES"
    elif not numbers:
        numbers = "OFF"

    def svgp(p):
        return (p[0], H - p[1])

    def pt(p):
        return f"{_fmt(p[0])},{_fmt(H - p[1])}"

    def line(a, b):
        return f'<path d="M {pt(a)} L {pt(b)}"/>'

    page_count = max(lay.page_count, 1)
    pages = [
        {"defs": [], "faces": [], "tabs": [], "cut": [], "mountain": [], "valley": [],
         "numbers": [], "tex_used": set(), "clip": []}
        for _ in range(page_count)
    ]
    pattern_n = [0]

    tab_on = {(t.face, t.corner): t for t in lay.tabs}

    def face_uvs(face, material):
        layer = uvs[material] if uvs and material < len(uvs) else None
        if layer and face.source < len(layer) and layer[face.source]:
            src_uv = layer[face.source]
            return [src_uv[c] for c in face.corners]
        return None

    def face_fill(page, face, pts):
        """Append the face's print fill: texture triangles or flat colour."""
        fill_poly(page, face.material, pts, face_uvs(face, face.material))

    def fill_poly(page, material, pts, face_uv):
        tex = None
        if textures and material < len(textures):
            tex = textures[material]
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
        if colors and material < len(colors) and colors[material]:
            col = _hex(colors[material])
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
            if page_images and page_images[place.page] is not None:
                for a_, b_, c_ in g.triangulate_2d(pts):
                    tri = g.grow_triangle([svgp(pts[a_]), svgp(pts[b_]), svgp(pts[c_])], bleed)
                    page["clip"].append(
                        f'<polygon points="{" ".join(f"{_fmt(x)},{_fmt(y)}" for x, y in tri)}"/>'
                    )
            elif fill_faces:
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
                numbered = numbers != "OFF" and info.kind == KIND_CUT and info.number
                if tab is not None:
                    tp = lay.tab_points(tab)
                    poly = f'<polygon points="{" ".join(pt(p) for p in tp)}"/>'
                    page["tabs"].append(poly)
                    if tab_texture or tab.outer:
                        page["clip"].append(poly)
                    baked_page = page_images and page_images[place.page] is not None
                    if tab_texture and not tab.outer and fill_faces and not baked_page:
                        # Continue the face's own art (or colour) across its tab.
                        fill_poly(page, face.material, tp, _extend_uvs(pts, face_uvs(face, face.material), k, tp))
                    if tab.outer and fill_faces and not baked_page:
                        # Outside tab: print the art of the face it covers.
                        partner = res.faces[tab.partner[0]]
                        p_uv = face_uvs(partner, partner.material)
                        tab_uv = None
                        if p_uv:
                            tab_uv = [
                                (sum(p_uv[c][0] * w for c, w in ws), sum(p_uv[c][1] * w for c, w in ws))
                                for ws in tab.weights
                            ]
                        fill_poly(page, partner.material, tp, tab_uv)
                    page["cut"].append(
                        f'<path d="M {pt(tp[0])} ' + " ".join(f"L {pt(p)}" for p in tp[1:]) + '"/>'
                    )
                    # The tab follows the face it glues to: same fold direction.
                    page["valley" if tab.angle < -1e-6 else "mountain"].append(line(a, b))
                    if numbered and numbers == "CLEAN" and tab.outer:
                        pass  # nothing printed on a visible tab
                    elif numbered and numbers == "CLEAN":
                        tc = g.polygon_centroid(tp)
                        page["numbers"].append(_number(a, b, info.number, H, at=tc, outward=True))
                else:
                    page["cut"].append(line(a, b))
                    if numbered and numbers == "CLEAN":
                        page["numbers"].append(_number(a, b, info.number, H, outside=True))
                if numbered and numbers == "FACES":
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
        baked = page_images[n - 1] if page_images and n - 1 < len(page_images) else None
        if baked is not None:
            parts.append('<clipPath id="pieces" clipPathUnits="userSpaceOnUse">')
            parts.extend(layers["clip"])
            parts.append("</clipPath>")
            layers["faces"].insert(0, (
                f'<image x="0" y="0" width="{_fmt(lay.page_w)}" height="{_fmt(lay.page_h)}" '
                f'preserveAspectRatio="none" clip-path="url(#pieces)" '
                f'xlink:href="{baked.href}" href="{baked.href}"/>'
            ))
        parts.append("</defs>")
        groups = [
            ("tabs", f"fill:{_hex(tab_color)};stroke:none" if tab_fill else "fill:none;stroke:none"),
            ("faces", ""),
            ("mountain", lines["mountain"]),
            ("valley", lines["valley"]),
            ("cut", lines["cut"]),
            ("numbers", "font-family:sans-serif;fill:#333333"),
        ]
        if content == "ART_CUT":
            groups = [(name, style) for name, style in groups if name not in ("mountain", "valley")]
        if lines_only:
            keep = {"CUT": ("cut",), "CREASE": ("mountain", "valley"),
                    "LINES": ("mountain", "valley", "cut")}[content]
            groups = [(name, style) for name, style in groups if name in keep]
        for name, style in groups:
            attr = f' style="{style}"' if style else ""
            parts.append(
                f'<g id="{name}" inkscape:groupmode="layer" inkscape:label="{name}"{attr}>'
            )
            parts.extend(layers[name])
            parts.append("</g>")
        if not lines_only:  # text would import into cutter software as shapes
            parts.append(
                f'<text x="{_fmt(lay.margin)}" y="{_fmt(H - lay.margin / 3)}" '
                f'style="font-family:sans-serif;font-size:2.5px;fill:#888888">{footer}</text>'
            )
        parts.append("</svg>")
        docs.append("\n".join(parts))
    return docs


def _extend_uvs(pts, face_uv, k, tab_pts):
    """UVs for a tab on edge k of a face: the face's own texture mapping
    carried on past that edge, so its art continues across the tab."""
    if not face_uv:
        return None
    n = len(pts)
    k1 = (k + 1) % n
    a, b = pts[k], pts[k1]
    best, j_best = 0.0, None
    for j in range(n):
        if j in (k, k1):
            continue
        area = abs((b[0] - a[0]) * (pts[j][1] - a[1]) - (pts[j][0] - a[0]) * (b[1] - a[1]))
        if area > best:
            best, j_best = area, j
    if j_best is None:
        return None
    m = g.affine_from_triangles([a, b, pts[j_best]], [face_uv[k], face_uv[k1], face_uv[j_best]])
    if m is None:
        return None
    ma, mb, mc, md, me, mf = m
    return [(ma * x + mc * y + me, mb * x + md * y + mf) for x, y in tab_pts]


def _number(a, b, number, page_h, at=None, outside=False, outward=False):
    """Glue number along edge a->b of a CCW face.

    default   just inside the face, reading from inside
    outside   on the scrap just beyond the cut line
    at        centred on a point (a tab), reading outwards"""
    d = g.sub2(b, a)
    length = g.len2(d)
    if length < 1e-6:
        return ""
    size = max(1.4, min(3.0, length * 0.3))
    dn = g.mul2(d, 1.0 / length)
    inside = (-dn[1], dn[0])  # left of a->b is inside a CCW face
    mid = g.mul2(g.add2(a, b), 0.5)
    angle = math.degrees(math.atan2(dn[1], dn[0]))
    if at is not None or outside:
        # Read with "up" pointing away from the piece.
        angle += 180.0
        if at is not None:
            # Baseline half a text-height "below" the tab centre.
            pos = g.add2(at, g.mul2(inside, size * 0.35))
        else:
            # Baseline just outside the cut; text grows away from the piece.
            pos = g.add2(mid, g.mul2(inside, -0.6))
    else:
        pos = g.add2(mid, g.mul2(inside, size * 0.35))
    x, y = pos[0], page_h - pos[1]
    return (
        f'<text x="{_fmt(x)}" y="{_fmt(y)}" text-anchor="middle" '
        f'style="font-size:{_fmt(size)}px" '
        f'transform="rotate({_fmt(-angle)} {_fmt(x)} {_fmt(y)})">{number}</text>'
    )
