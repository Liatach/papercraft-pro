# SPDX-License-Identifier: GPL-3.0-or-later
"""Turn Blender images into self-contained SVG textures (data: URIs)."""

import base64
import math
import os
import struct
import zlib

import bpy

from .core.svg import Texture

MIME = {"PNG": "image/png", "JPEG": "image/jpeg"}


def _file_bytes(img):
    """Original PNG/JPEG bytes when the image is unchanged on disk/packed."""
    fmt = img.file_format
    if fmt not in MIME or img.is_dirty or img.source != "FILE":
        return None, None
    if img.packed_file is not None:
        return bytes(img.packed_file.data), MIME[fmt]
    path = bpy.path.abspath(img.filepath_raw or img.filepath)
    if path and os.path.isfile(path):
        with open(path, "rb") as fh:
            return fh.read(), MIME[fmt]
    return None, None


def _png(width, height, rgba_rows):
    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + row for row in rgba_rows)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )


def to_srgb(x):
    import numpy as np

    x = np.clip(x, 0.0, 1.0)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1 / 2.4) - 0.055)


def from_srgb(x):
    import numpy as np

    x = np.clip(x, 0.0, 1.0)
    return np.where(x <= 0.04045, x / 12.92, np.power((x + 0.055) / 1.055, 2.4))


def polygon_mask(width, height, page_w, page_h, polygons):
    """Coverage mask (rows bottom-up, like Blender pixels) of convex
    polygons given in page millimetres, 2x2 supersampled."""
    import numpy as np

    mask = np.zeros((height, width), dtype=np.float32)
    sx, sy = width / page_w, height / page_h
    offsets = ((0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75))
    for poly in polygons:
        if len(poly) < 3:
            continue
        xs = [p[0] * sx for p in poly]
        ys = [p[1] * sy for p in poly]
        x0, x1 = max(0, int(min(xs))), min(width, int(max(xs)) + 1)
        y0, y1 = max(0, int(min(ys))), min(height, int(max(ys)) + 1)
        if x0 >= x1 or y0 >= y1:
            continue
        area = sum(xs[k - 1] * ys[k] - xs[k] * ys[k - 1] for k in range(len(xs)))  # >0: CCW
        sign = -1.0 if area > 0 else 1.0
        gx, gy = np.meshgrid(np.arange(x0, x1, dtype=np.float32),
                             np.arange(y0, y1, dtype=np.float32))
        cover = np.zeros(gx.shape, dtype=np.float32)
        for ox, oy in offsets:
            px, py = gx + ox, gy + oy
            inside = np.ones(gx.shape, dtype=bool)
            for k in range(len(xs)):
                ax, ay, bx, by = xs[k - 1], ys[k - 1], xs[k], ys[k]
                cross = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
                inside &= cross * sign <= 1e-6
            cover += inside
        sub = mask[y0:y1, x0:x1]
        np.maximum(sub, cover * 0.25, out=sub)
    return mask


def _encode_pixels(img, max_size, srgb_steps=None, alpha_mask=None):
    """PNG bytes of `img`. `srgb_steps`: how many times to apply the sRGB
    curve to its pixels before writing: 1 for linear values, 0 for values
    already in sRGB, -1 to undo one extra encoding. None: go by the buffer
    type (float buffers are linear)."""
    import numpy as np

    w, h = img.size
    px = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    arr = px.reshape(h, w, 4)
    if alpha_mask is not None:
        arr[..., 3] *= alpha_mask
    arr = arr[::-1]  # Blender stores rows bottom-up
    step = max(1, math.ceil(max(w, h) / max_size))
    if step > 1:
        arr = arr[::step, ::step]
    if srgb_steps is None:
        srgb_steps = 1 if img.is_float else 0
    if srgb_steps:
        arr = arr.copy()
        curve = to_srgb if srgb_steps > 0 else from_srgb
        for _ in range(abs(srgb_steps)):
            arr[..., :3] = curve(arr[..., :3])
    out = np.clip(arr * 255.0 + 0.5, 0, 255).astype(np.uint8)
    rows = [out[r].tobytes() for r in range(out.shape[0])]
    return _png(out.shape[1], out.shape[0], rows)


def image_to_texture(img, max_size=2048):
    if img is None:
        return None
    try:
        w, h = img.size
        if w == 0 or h == 0:
            return None
        data, mime = _file_bytes(img)
        if data is None or max(w, h) > max_size:
            data, mime = _encode_pixels(img, max_size), "image/png"
    except Exception as exc:  # missing file, unsupported format...
        print(f"Papercraft: could not read image {img.name!r}: {exc}")
        return None
    uri = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
    return Texture(uri, w, h)


def textures_for(images, max_size=2048):
    """Per material slot Texture (shared when slots use the same image)."""
    cache = {}
    out = []
    for img in images:
        if img is None:
            out.append(None)
            continue
        if img.name not in cache:
            cache[img.name] = image_to_texture(img, max_size)
        out.append(cache[img.name])
    return out


def decode_data_uri(href):
    """(width, height, rgb bytes, alpha bytes or None), rows top-down, for a
    data: URI PNG. Unfiltered RGBA PNGs (the add-on's own) are read
    directly; anything else goes through Blender's image loader."""
    import numpy as np
    import tempfile

    head, _, b64 = href.partition(",")
    data = base64.b64decode(b64)
    arr = _read_plain_png(data)
    if arr is None:
        ext = ".jpg" if "jpeg" in head else ".png"
        fd, path = tempfile.mkstemp(suffix=ext)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            img = bpy.data.images.load(path, check_existing=False)
            try:
                w, h = img.size
                px = np.empty(w * h * 4, dtype=np.float32)
                img.pixels.foreach_get(px)
                if img.is_float:
                    px = px.reshape(-1, 4)
                    px[:, :3] = to_srgb(px[:, :3])
                arr = np.clip(px.reshape(h, w, 4)[::-1] * 255.0 + 0.5, 0, 255).astype(np.uint8)
            finally:
                bpy.data.images.remove(img)
        finally:
            os.remove(path)
    h, w = arr.shape[:2]
    rgb = np.ascontiguousarray(arr[..., :3]).tobytes()
    alpha = arr[..., 3]
    return w, h, rgb, (None if alpha.min() == 255 else np.ascontiguousarray(alpha).tobytes())


def _read_plain_png(data):
    """8-bit RGBA, non-interlaced, every row filter 0 -> HxWx4 uint8; else None."""
    import numpy as np

    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    pos, idat, ihdr = 8, [], None
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        tag = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        if tag == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", body)
        elif tag == b"IDAT":
            idat.append(body)
        elif tag == b"IEND":
            break
        pos += 12 + length
    if ihdr is None or ihdr[2:5] != (8, 6, 0) or ihdr[6] != 0:
        return None
    w, h = ihdr[0], ihdr[1]
    raw = np.frombuffer(zlib.decompress(b"".join(idat)), dtype=np.uint8)
    if raw.size != h * (w * 4 + 1):
        return None
    rows = raw.reshape(h, w * 4 + 1)
    if rows[:, 0].any():
        return None
    return rows[:, 1:].reshape(h, w, 4)
