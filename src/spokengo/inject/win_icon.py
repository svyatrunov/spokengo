"""Extract the icon of a window's application as a PIL image (Windows only).

Used by the recording overlay to show *where* the transcription will be pasted.
Everything is lazily imported and fail-safe: on any error (no Pillow, no icon,
non-Windows) it returns None and the overlay falls back to a letter avatar.
"""
from __future__ import annotations

import sys
from typing import Optional

IS_WINDOWS = sys.platform.startswith("win")


def _hicon_to_image(hicon, size: int = 32):  # pragma: no cover - needs Windows
    import win32con
    import win32gui
    import win32ui
    from PIL import Image

    hdc = win32ui.CreateDCFromHandle(win32gui.GetDC(0))
    hbmp = win32ui.CreateBitmap()
    hbmp.CreateCompatibleBitmap(hdc, size, size)
    mem = hdc.CreateCompatibleDC()
    mem.SelectObject(hbmp)
    mem.DrawIcon((0, 0), hicon)
    bmpinfo = hbmp.GetInfo()
    bmpstr = hbmp.GetBitmapBits(True)
    img = Image.frombuffer("RGBA", (bmpinfo["bmWidth"], bmpinfo["bmHeight"]),
                           bmpstr, "raw", "BGRA", 0, 1)
    try:
        win32gui.DeleteObject(hbmp.GetHandle())
        mem.DeleteDC()
    except Exception:
        pass
    return img


def get_icon_image(hwnd, size: int = 32):  # pragma: no cover - needs Windows
    """Return a PIL.Image of the app icon for ``hwnd``, or None on failure."""
    if not IS_WINDOWS or not hwnd:
        return None
    try:
        import win32api
        import win32con
        import win32gui
        import win32process

        # 1) ask the window for its icon
        hicon = win32gui.SendMessage(hwnd, win32con.WM_GETICON, 1, 0)  # ICON_BIG
        if not hicon:
            hicon = win32gui.SendMessage(hwnd, win32con.WM_GETICON, 0, 0)
        if not hicon:
            try:
                hicon = win32gui.GetClassLong(hwnd, win32con.GCL_HICON)
            except Exception:
                hicon = 0
        if hicon:
            return _hicon_to_image(hicon, size)

        # 2) fall back to extracting the icon from the executable
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        h = win32api.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        exe = win32process.GetModuleFileNameEx(h, 0)
        large, _small = win32gui.ExtractIconEx(exe, 0)
        if large:
            img = _hicon_to_image(large[0], size)
            for ic in large:
                win32gui.DestroyIcon(ic)
            return img
    except Exception:
        return None
    return None


def get_process_name(hwnd) -> str:  # pragma: no cover - needs Windows
    if not IS_WINDOWS or not hwnd:
        return ""
    try:
        import os
        import win32api
        import win32process
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        h = win32api.OpenProcess(0x1000, False, pid)
        return os.path.basename(win32process.GetModuleFileNameEx(h, 0))
    except Exception:
        return ""
