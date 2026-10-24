# SPDX-License-Identifier: GPL-3.0-or-later
"""Write the add-on's SVG pages as one print-ready PDF.

Only the small SVG vocabulary that core.svg writes is understood: layers
(<g> with a style), polygons (flat fills or texture patterns), M/L paths,
glue-number text, the footer, and page images (optionally clipped). Lines
stay vectors, art is embedded at full resolution, every page is one PDF
page at its true size in millimetres.

decode_image(data_uri) -> (width, height, rgb_bytes, alpha_bytes_or_None)
is supplied by the caller (Blender reads any PNG); JPEGs are embedded as is.
"""

import base64
import re
import xml.etree.ElementTree as ET
import zlib

MM = 72.0 / 25.4  # PDF points per millimetre
SVG_NS = "{http://www.w3.org/2000/svg}"
XLINK = "{http://www.w3.org/1999/xlink}href"
HELVETICA_DIGIT = 0.556  # advance width of every digit, in em


def _num(v):
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def _color(value):
    value = (value or "").strip()
    if not value.startswith("#") or len(value) != 7:
        return None
    return tuple(int(value[i:i + 2], 16) / 255.0 for i in (1, 3, 5))


def _style(el, inherited):
    st = dict(inherited)
    for key in ("fill", "stroke", "stroke-width", "stroke-opacity", "fill-opacity",
                "stroke-dasharray", "stroke-linejoin", "stroke-linecap"):
        if el.get(key) is not None:
            st[key] = el.get(key)
    for part in (el.get("style") or "").split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            st[k.strip()] = v.strip()
    return st


def _points(text):
    vals = [float(v) for v in re.split(r"[\s,]+", text.strip()) if v]
    return list(zip(vals[0::2], vals[1::2]))


def _path_points(d):
    """Subpaths of an M/L-only path."""
    subs, cur = [], None
    for cmd, args in re.findall(r"([MLZ])([^MLZ]*)", d):
        if cmd == "M":
            cur = _points(args)
            subs.append(cur)
        elif cmd == "L" and cur is not None:
            cur.extend(_points(args))
    return subs


def _jpeg_info(data):
    i = 2
    while i < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2):
            h = (data[i + 5] << 8) | data[i + 6]
            w = (data[i + 7] << 8) | data[i + 8]
            return w, h, data[i + 9]
        i += 2 + ((data[i + 2] << 8) | data[i + 3])
    return None


class _Writer:
    def __init__(self):
        self.objects = []

    def add(self, body):
        self.objects.append(body)
        return len(self.objects)

    def reserve(self):
        self.objects.append(None)
        return len(self.objects)

    def set(self, num, body):
        self.objects[num - 1] = body

    @staticmethod
    def stream(dict_entries, data, compress=True):
        if compress:
            data = zlib.compress(data, 6)
            dict_entries = dict_entries + " /Filter /FlateDecode"
        return (f"<< {dict_entries} /Length {len(data)} >>\nstream\n".encode("latin-1")
                + data + b"\nendstream")

    def bytes(self, root):
        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for n, body in enumerate(self.objects, 1):
            offsets.append(len(out))
            if isinstance(body, str):
                body = body.encode("latin-1")
            out += f"{n} 0 obj\n".encode() + body + b"\nendobj\n"
        xref = len(out)
        out += f"xref\n0 {len(self.objects) + 1}\n0000000000 65535 f \n".encode()
        for off in offsets:
            out += f"{off:010d} 00000 n \n".encode()
        out += (f"trailer\n<< /Size {len(self.objects) + 1} /Root {root} 0 R >>\n"
                f"startxref\n{xref}\n%%EOF\n").encode()
        return bytes(out)


def svgs_to_pdf(docs, decode_image, title="Papercraft"):
    w = _Writer()
    catalog = w.reserve()
    pages_obj = w.reserve()
    font = w.add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
                 "/Encoding /WinAnsiEncoding >>")
    images = {}  # data URI -> (object number, width, height)
    gstates = {}  # (stroke alpha, fill alpha) -> name
    page_refs = []

    def image_obj(href):
        if href in images:
            return images[href]
        head, _, b64 = href.partition(",")
        data = base64.b64decode(b64)
        if "jpeg" in head or "jpg" in head:
            info = _jpeg_info(data)
            if info is None:
                images[href] = None
                return None
            iw, ih, comps = info
            space = {1: "/DeviceGray", 3: "/DeviceRGB", 4: "/DeviceCMYK"}.get(comps, "/DeviceRGB")
            num = w.add(w.stream(f"/Type /XObject /Subtype /Image /Width {iw} /Height {ih} "
                                 f"/ColorSpace {space} /BitsPerComponent 8 /Filter /DCTDecode",
                                 data, compress=False))
        else:
            decoded = decode_image(href)
            if decoded is None:
                images[href] = None
                return None
            iw, ih, rgb, alpha = decoded
            smask = ""
            if alpha is not None:
                sm = w.add(w.stream(f"/Type /XObject /Subtype /Image /Width {iw} /Height {ih} "
                                    "/ColorSpace /DeviceGray /BitsPerComponent 8", alpha))
                smask = f" /SMask {sm} 0 R"
            num = w.add(w.stream(f"/Type /XObject /Subtype /Image /Width {iw} /Height {ih} "
                                 f"/ColorSpace /DeviceRGB /BitsPerComponent 8{smask}", rgb))
        images[href] = (num, iw, ih)
        return images[href]

    def gstate(sa, fa):
        key = (round(sa, 3), round(fa, 3))
        if key not in gstates:
            gstates[key] = f"G{len(gstates)}"
        return gstates[key]

    for doc in docs:
        root = ET.fromstring(doc.encode("utf-8"))
        vb = [float(v) for v in root.get("viewBox").split()]
        page_w, page_h = vb[2], vb[3]
        defs = {}
        for el in root.iter():
            if el.get("id") is not None:
                defs[el.get("id")] = el
        ops = [f"{_num(MM)} 0 0 {_num(-MM)} 0 {_num(page_h * MM)} cm"]  # mm, y down
        used_images = {}

        def xobj(href):
            info = image_obj(href)
            if info is None:
                return None
            name = f"Im{info[0]}"
            used_images[name] = info[0]
            return name, info[1], info[2]

        def set_alpha(st):
            sa = float(st.get("stroke-opacity", 1) or 1)
            fa = float(st.get("fill-opacity", 1) or 1)
            ops.append(f"/{gstate(sa, fa)} gs")

        def poly_path(pts, close=True):
            s = f"{_num(pts[0][0])} {_num(pts[0][1])} m " + " ".join(
                f"{_num(x)} {_num(y)} l" for x, y in pts[1:])
            return s + (" h" if close else "")

        def stroke_setup(st):
            col = _color(st.get("stroke"))
            if col is None:
                return False
            ops.append("{} {} {} RG".format(*(_num(c) for c in col)))
            ops.append(f"{_num(float(st.get('stroke-width', 1)))} w")
            ops.append({"round": "1", "bevel": "2"}.get(st.get("stroke-linejoin"), "0") + " j")
            ops.append({"round": "1", "square": "2"}.get(st.get("stroke-linecap"), "0") + " J")
            dash = st.get("stroke-dasharray")
            if dash and dash != "none":
                ops.append("[" + " ".join(_num(float(v)) for v in re.split(r"[\s,]+", dash) if v) + "] 0 d")
            else:
                ops.append("[] 0 d")
            return True

        def draw_image(href, x, y, iw, ih, clip=None):
            ref = xobj(href)
            if ref is None:
                return
            ops.append("q")
            if clip is not None:
                for poly in clip:
                    ops.append(poly_path(poly))
                ops.append("W n")
            ops.append(f"{_num(iw)} 0 0 {_num(-ih)} {_num(x)} {_num(y + ih)} cm /{ref[0]} Do Q")

        def draw_pattern_poly(pts, pattern):
            use = next(iter(pattern), None)
            if use is None:
                return
            img = defs.get((use.get("href") or use.get(XLINK) or "").lstrip("#"))
            if img is None:
                return
            href = img.get("href") or img.get(XLINK)
            m = [float(v) for v in re.findall(r"[-\d.eE+]+", pattern.get("patternTransform", ""))]
            if len(m) != 6:
                return
            ref = xobj(href)
            if ref is None:
                return
            iw, ih = float(img.get("width")), float(img.get("height"))
            ops.append("q " + poly_path(pts) + " W n")
            ops.append(" ".join(_num(v) for v in m) + " cm")
            ops.append(f"{_num(iw)} 0 0 {_num(-ih)} 0 {_num(ih)} cm /{ref[0]} Do Q")

        def draw_text(el, st):
            text = (el.text or "").strip()
            if not text:
                return
            col = _color(st.get("fill")) or (0, 0, 0)
            size = float(re.sub(r"[^\d.]", "", st.get("font-size", "3")) or 3)
            x, y = float(el.get("x", 0)), float(el.get("y", 0))
            angle = 0.0
            rot = re.search(r"rotate\(([-\d.eE+]+)", el.get("transform", ""))
            if rot:
                angle = float(rot.group(1))
            import math

            c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
            width = 0.0
            if el.get("text-anchor") == "middle":
                width = len(text) * (HELVETICA_DIGIT if text.isdigit() else 0.5) * size
            safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            safe = safe.encode("cp1252", "replace").decode("latin-1")
            ops.append("{} {} {} rg".format(*(_num(v) for v in col)))
            ops.append(f"BT /F1 {_num(size)} Tf {_num(c)} {_num(s)} {_num(s)} {_num(-c)} "
                       f"{_num(x)} {_num(y)} Tm {_num(-width / 2)} 0 Td ({safe}) Tj ET")

        def walk(el, st):
            tag = el.tag.replace(SVG_NS, "")
            if tag in ("defs", "title", "clipPath", "pattern"):
                return
            st = _style(el, st)
            if tag in ("svg", "g"):
                for child in el:
                    walk(child, st)
            elif tag == "polygon":
                pts = _points(el.get("points", ""))
                if len(pts) < 3:
                    return
                fill = st.get("fill", "#000000")
                if fill.startswith("url(#"):
                    pat = defs.get(fill[5:-1])
                    if pat is not None:
                        draw_pattern_poly(pts, pat)
                    return
                set_alpha(st)
                col = _color(fill)
                stroked = stroke_setup(st) if st.get("stroke", "none") != "none" else False
                if col is not None:
                    ops.append("{} {} {} rg".format(*(_num(v) for v in col)))
                if col is not None or stroked:
                    ops.append(poly_path(pts) + (" B" if col and stroked else " f" if col else " S"))
            elif tag == "path":
                if st.get("stroke", "none") == "none":
                    return
                set_alpha(st)
                if not stroke_setup(st):
                    return
                for sub in _path_points(el.get("d", "")):
                    if len(sub) >= 2:
                        ops.append(poly_path(sub, close=False) + " S")
            elif tag == "image":
                href = el.get("href") or el.get(XLINK)
                clip = None
                cp = re.match(r"url\(#(.+)\)", el.get("clip-path", "") or "")
                if cp and cp.group(1) in defs:
                    clip = [_points(p.get("points", "")) for p in defs[cp.group(1)]
                            if p.get("points")]
                set_alpha(st)
                draw_image(href, float(el.get("x", 0)), float(el.get("y", 0)),
                           float(el.get("width")), float(el.get("height")), clip)
            elif tag == "text":
                set_alpha(st)
                draw_text(el, st)

        walk(root, {"fill": "#000000", "stroke": "none"})

        content = w.add(w.stream("", "\n".join(ops).encode("latin-1")))
        xobjects = " ".join(f"/{k} {v} 0 R" for k, v in used_images.items())
        page_refs.append(w.add(
            f"<< /Type /Page /Parent {pages_obj} 0 R "
            f"/MediaBox [0 0 {_num(page_w * MM)} {_num(page_h * MM)}] "
            f"/Contents {content} 0 R /Resources << /Font << /F1 {font} 0 R >> "
            f"/XObject << {xobjects} >> /ExtGState GSTATES >> >>"
        ))

    # Graphics states are shared by all pages: patch them in now they're known.
    gs = " ".join(f"/{name} << /Type /ExtGState /CA {_num(sa)} /ca {_num(fa)} >>"
                  for (sa, fa), name in gstates.items())
    for ref in page_refs:
        w.set(ref, w.objects[ref - 1].replace("GSTATES", f"<< {gs} >>"))
    kids = " ".join(f"{r} 0 R" for r in page_refs)
    w.set(pages_obj, f"<< /Type /Pages /Kids [{kids}] /Count {len(page_refs)} >>")
    safe_title = title.replace("\\", "").replace("(", "").replace(")", "")
    info = w.add(f"<< /Title ({safe_title.encode('ascii', 'replace').decode()}) "
                 "/Creator (Papercraft Pro) >>")
    w.set(catalog, f"<< /Type /Catalog /Pages {pages_obj} 0 R >>")
    data = w.bytes(catalog)
    # Point the trailer at the info dictionary too.
    return data.replace(b"trailer\n<< ", f"trailer\n<< /Info {info} 0 R ".encode(), 1)
