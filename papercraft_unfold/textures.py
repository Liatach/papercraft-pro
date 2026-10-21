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


def _encode_pixels(img, max_size):
    import numpy as np

    w, h = img.size
    px = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    arr = px.reshape(h, w, 4)[::-1]  # Blender stores rows bottom-up
    step = max(1, math.ceil(max(w, h) / max_size))
    if step > 1:
        arr = arr[::step, ::step]
    if img.is_float:  # linear float data -> sRGB for printing
        rgb = np.clip(arr[..., :3], 0.0, 1.0)
        arr = arr.copy()
        arr[..., :3] = np.where(
            rgb <= 0.0031308, rgb * 12.92, 1.055 * np.power(rgb, 1 / 2.4) - 0.055
        )
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
