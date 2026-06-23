"""Recording overlay: Pillow-rendered rounded pill shown via a Windows layered
window (per-pixel alpha). Smooth corners, soft shadow, gradient. The recording
indicator is an animated "sonar ping" around a steady dot (elegant, not a harsh
pulse). Shows WHERE the text will paste (current foreground app, live) plus a
muted hint: Enter — отправить · Esc — отмена.
"""
from __future__ import annotations

import logging
import math
import sys
from typing import Callable, Optional

from .state import State

IS_WINDOWS = sys.platform.startswith("win")
log = logging.getLogger("spokengo.overlay")

# layout
_M = 11            # shadow margin
_PAD = 13
_DOT = 9
_GAP = 9
_ICON = 16
_ROW1 = 19
_ROWGAP = 3
_ROW2 = 13
_PILL_H = 8 + _ROW1 + _ROWGAP + _ROW2 + 8
_RADIUS = 14
# palette
_TOP = (48, 55, 76)
_BOTTOM = (25, 29, 45)
_TEXT = (237, 240, 246)
_HINT = (150, 158, 178)
_REC = (244, 63, 94)
_BUSY = (245, 165, 36)
_HINT_TEXT = "Enter — отправить   ·   Esc — отмена"
_RING_MAX = 13


def display_name(target) -> str:
    if target is None:
        return "активное поле"
    name = getattr(target, "process_name", "") or ""
    if name:
        if "." in name:
            name = name.rsplit(".", 1)[0]
        return name[:22]
    title = getattr(target, "title", "") or ""
    return (title[:22] or "активное поле")


def _load_font(size: int):
    from PIL import ImageFont
    for path in ("C:/Windows/Fonts/segoeui.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _gradient_rounded(w, h, top, bottom, r):
    from PIL import Image, ImageDraw
    grad = Image.new("RGB", (w, h))
    px = grad.load()
    for y in range(h):
        t = y / (h - 1) if h > 1 else 0
        col = (int(top[0] + (bottom[0] - top[0]) * t),
               int(top[1] + (bottom[1] - top[1]) * t),
               int(top[2] + (bottom[2] - top[2]) * t))
        for x in range(w):
            px[x, y] = col
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=r, fill=255)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.paste(grad, (0, 0), mask)
    return out


def render_base(name: str, state: str = "recording", icon_img=None):
    """Static pill (everything but the animated dot). Returns (image, dot_xy)."""
    from PIL import Image, ImageDraw, ImageFilter
    f_name = _load_font(13)
    f_hint = _load_font(10)
    f_busy = _load_font(13)
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))

    recording = state != "transcribing"
    line1 = name if recording else "Распознаю…"
    line2 = _HINT_TEXT if recording else name
    f1 = f_name if recording else f_busy
    icon_on_row1 = recording

    w1 = (_PAD + _DOT + _GAP + (_ICON + _GAP if icon_on_row1 and icon_img else 0)
          + int(measure.textlength(line1, font=f1)) + _PAD)
    w2 = _PAD + (_ICON + _GAP if (not icon_on_row1 and icon_img) else 0) \
        + int(measure.textlength(line2, font=f_hint)) + _PAD
    wp = max(132, min(280, max(w1, w2)))
    W, Himg = wp + 2 * _M, _PILL_H + 2 * _M

    img = Image.new("RGBA", (W, Himg), (0, 0, 0, 0))
    shadow = Image.new("RGBA", (W, Himg), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        [_M, _M + 3, _M + wp, _M + _PILL_H + 3], radius=_RADIUS, fill=(0, 0, 0, 120))
    img = Image.alpha_composite(img, shadow.filter(ImageFilter.GaussianBlur(6)))
    img.alpha_composite(_gradient_rounded(wp, _PILL_H, _TOP, _BOTTOM, _RADIUS), (_M, _M))

    draw = ImageDraw.Draw(img)
    x0 = _M + _PAD
    row1_cy = _M + 8 + _ROW1 // 2
    row2_cy = _M + 8 + _ROW1 + _ROWGAP + _ROW2 // 2
    dot_xy = (x0 + _DOT // 2, row1_cy)

    x = x0 + _DOT + _GAP
    if icon_on_row1 and icon_img is not None:
        try:
            img.alpha_composite(icon_img.convert("RGBA").resize((_ICON, _ICON)),
                                (x, row1_cy - _ICON // 2))
            x += _ICON + _GAP
        except Exception:
            pass
    line1_fill = (_TEXT + (255,)) if recording else (_BUSY + (255,))
    draw.text((x, row1_cy + 1), line1, font=f1, fill=line1_fill, anchor="lm")

    x = x0
    if (not icon_on_row1) and icon_img is not None:
        try:
            img.alpha_composite(icon_img.convert("RGBA").resize((_ICON, _ICON)),
                                (x, row2_cy - _ICON // 2))
            x += _ICON + _GAP
        except Exception:
            pass
    draw.text((x, row2_cy + 1), line2, font=f_hint, fill=_HINT + (255,), anchor="lm")
    return img, dot_xy


def frame_image(base, dot_xy, state: str, phase: float):
    """base + animated sonar-ping rings + steady core dot at the given phase."""
    from PIL import Image, ImageDraw, ImageFilter
    color = _BUSY if state == "transcribing" else _REC
    img = base.copy()
    cx, cy = dot_xy
    tile = Image.new("RGBA", (2 * _RING_MAX + 6, 2 * _RING_MAX + 6), (0, 0, 0, 0))
    td = ImageDraw.Draw(tile)
    tc = tile.width // 2
    r0 = _DOT / 2 + 1
    for k in (0.0, 0.5):
        p = (phase + k) % 1.0
        rr = r0 + (_RING_MAX - r0) * p
        a = int(150 * (1.0 - p) ** 1.3)
        if a > 2:
            td.ellipse([tc - rr, tc - rr, tc + rr, tc + rr],
                       outline=color + (a,), width=2)
    tile = tile.filter(ImageFilter.GaussianBlur(0.6))
    img.alpha_composite(tile, (int(cx - tc), int(cy - tc)))
    # steady core with a gentle breath
    br = _DOT / 2 + 0.6 * (0.5 + 0.5 * math.sin(phase * 2 * math.pi))
    d = ImageDraw.Draw(img)
    d.ellipse([cx - br, cy - br, cx + br, cy + br], fill=color + (255,))
    return img


class NullOverlay:
    def show(self, target=None) -> None: ...
    def set_state(self, state, message: str = "") -> None: ...
    def hide(self) -> None: ...


class _LayeredWin:
    """A real native Windows layered window (no Tk Toplevel) — created with
    pywin32, painted with UpdateLayeredWindow. Using a dedicated native window
    removes the Tk geometry/repaint fight that caused the flicker, and we set the
    correct ctypes handle types so 64-bit HDC/HBITMAP handles are not truncated
    (the real source of the intermittent flicker)."""
    _registered = False
    _classname = "SpokenGoOverlayWnd"

    def __init__(self):
        import win32api
        import win32con
        import win32gui
        self._win32gui = win32gui
        self._win32con = win32con
        hinst = win32api.GetModuleHandle(None)
        if not _LayeredWin._registered:
            wc = win32gui.WNDCLASS()
            wc.lpszClassName = _LayeredWin._classname
            wc.lpfnWndProc = {}          # default window procedure
            wc.hInstance = hinst
            try:
                win32gui.RegisterClass(wc)
            except Exception:
                pass
            _LayeredWin._registered = True
        ex = (win32con.WS_EX_LAYERED | win32con.WS_EX_NOACTIVATE
              | win32con.WS_EX_TOOLWINDOW | win32con.WS_EX_TOPMOST)
        self.hwnd = win32gui.CreateWindowEx(
            ex, _LayeredWin._classname, "SpokenGo", win32con.WS_POPUP,
            0, 0, 10, 10, 0, 0, hinst, None)
        self._init_ctypes()

    def _init_ctypes(self):
        import ctypes
        from ctypes import wintypes
        self._ctypes = ctypes
        u = ctypes.windll.user32
        g = ctypes.windll.gdi32
        # correct handle types so 64-bit pointers are not truncated
        u.GetDC.restype = wintypes.HDC; u.GetDC.argtypes = [wintypes.HWND]
        u.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        g.CreateCompatibleDC.restype = wintypes.HDC
        g.CreateCompatibleDC.argtypes = [wintypes.HDC]
        g.CreateDIBSection.restype = wintypes.HBITMAP
        g.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                       ctypes.POINTER(ctypes.c_void_p),
                                       wintypes.HANDLE, wintypes.DWORD]
        g.SelectObject.restype = wintypes.HGDIOBJ
        g.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        g.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        g.DeleteDC.argtypes = [wintypes.HDC]

        class BMIH(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                        ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                        ("biClrImportant", wintypes.DWORD)]

        class BLEND(ctypes.Structure):
            _fields_ = [("BlendOp", ctypes.c_byte), ("BlendFlags", ctypes.c_byte),
                        ("SourceConstantAlpha", ctypes.c_byte), ("AlphaFormat", ctypes.c_byte)]

        u.UpdateLayeredWindow.argtypes = [
            wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT),
            ctypes.POINTER(wintypes.SIZE), wintypes.HDC,
            ctypes.POINTER(wintypes.POINT), wintypes.DWORD,
            ctypes.POINTER(BLEND), wintypes.DWORD]
        u.UpdateLayeredWindow.restype = wintypes.BOOL
        self._u = u; self._g = g; self._BMIH = BMIH; self._BLEND = BLEND

    def paint(self, img, x, y):
        from PIL import Image, ImageChops
        ctypes = self._ctypes
        from ctypes import wintypes
        w, h = img.size
        r, g, b, a = img.split()
        pm = Image.merge("RGBA", (ImageChops.multiply(r, a), ImageChops.multiply(g, a),
                                  ImageChops.multiply(b, a), a))
        data = pm.tobytes("raw", "BGRA")
        bmi = self._BMIH()
        bmi.biSize = ctypes.sizeof(self._BMIH); bmi.biWidth = w; bmi.biHeight = -h
        bmi.biPlanes = 1; bmi.biBitCount = 32; bmi.biCompression = 0
        screen = self._u.GetDC(None)
        memdc = self._g.CreateCompatibleDC(screen)
        ppv = ctypes.c_void_p()
        hbmp = self._g.CreateDIBSection(screen, ctypes.byref(bmi), 0,
                                        ctypes.byref(ppv), None, 0)
        ctypes.memmove(ppv, data, len(data))
        old = self._g.SelectObject(memdc, hbmp)
        size = wintypes.SIZE(w, h); psrc = wintypes.POINT(0, 0)
        pdst = wintypes.POINT(int(x), int(y))
        blend = self._BLEND(0, 0, 255, 1)
        ok = self._u.UpdateLayeredWindow(self.hwnd, screen, ctypes.byref(pdst),
                                         ctypes.byref(size), memdc, ctypes.byref(psrc),
                                         0, ctypes.byref(blend), 2)
        if not ok:
            log.error("UpdateLayeredWindow failed: %s",
                      ctypes.windll.kernel32.GetLastError())
        self._g.SelectObject(memdc, old); self._g.DeleteObject(hbmp)
        self._g.DeleteDC(memdc); self._u.ReleaseDC(None, screen)

    def show(self):
        self._win32gui.ShowWindow(self.hwnd, self._win32con.SW_SHOWNA)

    def hide(self):
        try:
            self._win32gui.ShowWindow(self.hwnd, self._win32con.SW_HIDE)
        except Exception:
            pass


class TkOverlay:
    def __init__(self, root):
        self._root = root
        self._win = None              # _LayeredWin (native) or None
        self._shown = False
        self._tracking = False
        self._state = "recording"
        self._name = ""
        self._icon = None
        self._handle = object()
        self._base = None
        self._dot = (0, 0)
        self._pos = (0, 0)
        self._phase = 0.0
        self.target_provider: Optional[Callable[[], object]] = None

    def show(self, target=None) -> None:
        self._root.after(0, lambda: self._show(target))

    def set_state(self, state, message: str = "") -> None:
        self._root.after(0, lambda: self._set_state(state))

    def hide(self) -> None:
        self._root.after(0, self._hide)

    def _ensure(self):
        if self._win is not None or not IS_WINDOWS:
            return
        try:
            self._win = _LayeredWin()
        except Exception:
            log.exception("native overlay window creation failed")
            self._win = None

    def _rebuild_base(self):
        try:
            self._base, self._dot = render_base(self._name, self._state, self._icon)
        except Exception:
            log.exception("overlay render failed")
            self._base = None
            return
        try:
            sw = self._root.winfo_screenwidth()
            sh = self._root.winfo_screenheight()
        except Exception:
            sw, sh = 1920, 1080
        W, H = self._base.size
        self._pos = ((sw - W) // 2, sh - 135)
        self._paint_frame()

    def _set_target_visual(self, target):
        self._name = display_name(target)
        self._icon = None
        handle = getattr(target, "handle", None) if target is not None else None
        try:
            from .win_icon import get_icon_image
            self._icon = get_icon_image(handle, 16) if handle else None
        except Exception:
            self._icon = None
        self._rebuild_base()

    def _paint_frame(self):
        if self._base is None or self._win is None:
            return
        try:
            frame = frame_image(self._base, self._dot, self._state, self._phase)
            self._win.paint(frame, self._pos[0], self._pos[1])
        except Exception:
            log.exception("overlay paint failed")

    def _show(self, target):
        try:
            log.info("overlay show: %s", getattr(target, "process_name", "?"))
            self._ensure()
            if self._win is None:
                return
            self._state = "recording"
            self._handle = getattr(target, "handle", None)
            self._set_target_visual(target)
            self._win.show()
            self._paint_frame()
            if not self._shown:
                self._shown = True
                self._tracking = True
                self._animate()
                self._poll()
        except Exception:
            log.exception("overlay show failed")

    def _set_state(self, state):
        if self._win is None:
            return
        if state is State.TRANSCRIBING:
            self._state = "transcribing"
            self._tracking = False
            self._rebuild_base()

    def _animate(self):
        if not self._shown or self._win is None or self._base is None:
            if self._shown:
                self._root.after(90, self._animate)
            return
        self._phase = (self._phase + 0.06) % 1.0
        self._paint_frame()
        self._root.after(90, self._animate)

    def _poll(self):
        if not self._shown or self._win is None:
            return
        if self._tracking and self.target_provider:
            try:
                t = self.target_provider()
            except Exception:
                t = None
            h = getattr(t, "handle", None) if t is not None else None
            if h != self._handle:
                self._handle = h
                self._set_target_visual(t)
        self._root.after(350, self._poll)

    def _hide(self):
        log.info("overlay hide")
        self._shown = False
        self._tracking = False
        if self._win is not None:
            self._win.hide()


def make_overlay(root=None):
    return NullOverlay() if root is None else TkOverlay(root)
