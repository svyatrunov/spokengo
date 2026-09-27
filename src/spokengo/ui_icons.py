"""Line icons and round shapes for the control panel, drawn with Pillow.

Tk's canvas does not antialias on Windows, so a 1.5 px stroke or a rounded
corner drawn there looks jagged. Everything that needs a smooth curve (icons,
the record button, the status panel's corners) is rendered here at 4x and
downsampled, then handed to Tk as a PNG ``PhotoImage``.

One icon family: 16 px grid, 1.6 px stroke, round caps, drawn only from lines,
arcs and rounded rectangles. Pillow is already a runtime dependency (the
overlay uses it); without it ``available()`` is False and the UI falls back to
plain text labels.
"""
from __future__ import annotations

import base64
import io
import math
from functools import lru_cache
from typing import Optional, Tuple

SS = 4  # supersampling factor


def available() -> bool:
    try:
        import PIL.Image  # noqa: F401
        import PIL.ImageDraw  # noqa: F401
        return True
    except Exception:
        return False


def _rgba(hex_color: Optional[str], alpha: int = 255) -> Tuple[int, int, int, int]:
    if not hex_color:
        return (0, 0, 0, 0)
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alpha)


def _png(img) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


# --------------------------------------------------------------------- glyphs
# Each glyph draws into a 16x16 unit box scaled by ``k`` (pixels per unit).
def _line(d, pts, k, col, w, ox, oy):
    d.line([(ox + x * k, oy + y * k) for x, y in pts], fill=col, width=w, joint="curve")
    r = w / 2
    for x, y in (pts[0], pts[-1]):                     # round caps
        cx, cy = ox + x * k, oy + y * k
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=col)


def _rrect(d, x0, y0, x1, y1, rad, k, col, w, ox, oy):
    d.rounded_rectangle((ox + x0 * k, oy + y0 * k, ox + x1 * k, oy + y1 * k),
                        radius=rad * k, outline=col, width=w)


def _arc_arrow(d, k, col, w, ox, oy, start, end, head_at_end=True):
    """Circular arrow around the box centre (used by retry and rescan)."""
    cx, cy, r = 8, 8, 5.2
    box = (ox + (cx - r) * k, oy + (cy - r) * k, ox + (cx + r) * k, oy + (cy + r) * k)
    d.arc(box, start=start, end=end, fill=col, width=w)
    ang = math.radians(end if head_at_end else start)
    tip = (cx + r * math.cos(ang), cy + r * math.sin(ang))
    # tangent direction (clockwise in screen coordinates)
    tx, ty = -math.sin(ang), math.cos(ang)
    if not head_at_end:
        tx, ty = -tx, -ty
    nx, ny = math.cos(ang), math.sin(ang)
    s = 2.6
    a = (tip[0] - tx * s + nx * s * 0.9, tip[1] - ty * s + ny * s * 0.9)
    b = (tip[0] - tx * s - nx * s * 0.9, tip[1] - ty * s - ny * s * 0.9)
    _line(d, [a, tip, b], k, col, w, ox, oy)


def _glyph(d, name, k, col, w, ox, oy):
    if name == "copy":
        _rrect(d, 5.5, 5.5, 13, 13, 1.8, k, col, w, ox, oy)
        _line(d, [(10.5, 3), (4.8, 3)], k, col, w, ox, oy)
        d.arc((ox + 3 * k, oy + 3 * k, ox + 6.6 * k, oy + 6.6 * k), 180, 270, fill=col, width=w)
        _line(d, [(3, 4.8), (3, 10.5)], k, col, w, ox, oy)
    elif name == "check":
        _line(d, [(3.5, 8.4), (6.6, 11.4), (12.6, 4.8)], k, col, w, ox, oy)
    elif name == "retry":
        _arc_arrow(d, k, col, w, ox, oy, 70, 350)
    elif name == "trash":
        _line(d, [(2.8, 4.5), (13.2, 4.5)], k, col, w, ox, oy)
        _line(d, [(6.2, 4.3), (6.6, 2.6), (9.4, 2.6), (9.8, 4.3)], k, col, w, ox, oy)
        _line(d, [(4.2, 4.8), (4.9, 13.2), (11.1, 13.2), (11.8, 4.8)], k, col, w, ox, oy)
        _line(d, [(6.8, 7.2), (6.8, 10.8)], k, col, w, ox, oy)
        _line(d, [(9.2, 7.2), (9.2, 10.8)], k, col, w, ox, oy)
    elif name == "close":
        _line(d, [(4.2, 4.2), (11.8, 11.8)], k, col, w, ox, oy)
        _line(d, [(11.8, 4.2), (4.2, 11.8)], k, col, w, ox, oy)
    elif name == "chevron":
        _line(d, [(4.5, 6.5), (8, 10), (11.5, 6.5)], k, col, w, ox, oy)
    elif name == "keep":            # bookmark: keep the audio
        _line(d, [(4.5, 13), (4.5, 3.2), (11.5, 3.2), (11.5, 13), (8, 10.2), (4.5, 13)],
              k, col, w, ox, oy)
    else:
        raise ValueError(f"unknown icon {name!r}")


@lru_cache(maxsize=256)
def icon_png(name: str, color: str, *, box: int = 28, glyph: int = 16,
             fill: Optional[str] = None, radius: int = 6, stroke: float = 1.6) -> str:
    """Base64 PNG: ``glyph``-px icon centred in a ``box``-px square, optionally
    on a rounded ``fill`` (hover state)."""
    from PIL import Image, ImageDraw
    big = box * SS
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if fill:
        d.rounded_rectangle((0, 0, big - 1, big - 1), radius=radius * SS, fill=_rgba(fill))
    k = glyph * SS / 16
    o = (big - glyph * SS) / 2
    _glyph(d, name, k, _rgba(color), max(1, round(stroke * SS)), o, o)
    return _png(img.resize((box, box), Image.LANCZOS))


@lru_cache(maxsize=64)
def record_png(state: str, *, size: int = 44, ring: str = "#34332f",
               dot: str = "#e5484d", fill: Optional[str] = None) -> str:
    """The round record button. ``state``: "dot" (ready) or "stop" (recording)."""
    from PIL import Image, ImageDraw
    big = size * SS
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((0, 0, big - 1, big - 1), fill=_rgba(fill or ring))
    c = big / 2
    if state == "stop":
        s = big * 0.15
        d.rounded_rectangle((c - s, c - s, c + s, c + s), radius=big * 0.05, fill=_rgba(dot))
    else:
        r = big * 0.17
        d.ellipse((c - r, c - r, c + r, c + r), fill=_rgba(dot))
    return _png(img.resize((size, size), Image.LANCZOS))


def rounded_panel_png(w: int, h: int, radius: int, fill: str, outer: str) -> str:
    """An antialiased rounded rectangle on an ``outer`` background."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (w * SS, h * SS), _rgba(outer))
    ImageDraw.Draw(img).rounded_rectangle(
        (0, 0, w * SS - 1, h * SS - 1), radius=radius * SS, fill=_rgba(fill))
    return _png(img.resize((w, h), Image.LANCZOS))
