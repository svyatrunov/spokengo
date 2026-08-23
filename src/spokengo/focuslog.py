"""Focus diagnostics: where the caret is, at each step of a dictation.

Written to chase "press Ctrl+Space and the caret leaves my text field". The one
rule here is that *observing must not disturb*: focus state for another
process's thread is read with GetGUIThreadInfo, never with AttachThreadInput —
attaching input queues is itself capable of clearing focus and destroying the
caret, so using it to measure would fake the very bug we are hunting.

Off unless ``debug_focus`` is set in config.toml. One line per step, e.g.

    focus [hotkey] fg=0x1a05c2 'Telegram' Chrome_WidgetWin_1 tid=8123
          focus=0x2b0114 Chrome_RenderWidgetHostHWND caret=0x2b0114 @(412,733)

A caret that is present at [hotkey] and gone at [after-capture] names the
culprit exactly.
"""
from __future__ import annotations

import logging
import sys

log = logging.getLogger("spokengo.focus")

IS_WINDOWS = sys.platform.startswith("win")
_enabled = False


def enable(on: bool = True) -> None:
    global _enabled
    _enabled = bool(on) and IS_WINDOWS


def is_enabled() -> bool:
    return _enabled


if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    class _RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class _GUITHREADINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                    ("hwndActive", wintypes.HWND), ("hwndFocus", wintypes.HWND),
                    ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
                    ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND),
                    ("rcCaret", _RECT)]


def _cls(hwnd) -> str:
    if not hwnd:
        return "-"
    try:
        buf = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetClassNameW(wintypes.HWND(hwnd), buf, 256)
        return buf.value or "?"
    except Exception:
        return "?"


def _title(hwnd) -> str:
    if not hwnd:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(160)
        ctypes.windll.user32.GetWindowTextW(wintypes.HWND(hwnd), buf, 160)
        return (buf.value or "")[:40]
    except Exception:
        return ""


def snapshot() -> dict:
    """Read the current focus/caret state. Never perturbs it."""
    if not IS_WINDOWS:
        return {}
    u = ctypes.windll.user32
    out: dict = {}
    try:
        fg = u.GetForegroundWindow()
        out["fg"] = int(fg or 0)
        out["fg_class"] = _cls(fg)
        out["fg_title"] = _title(fg)
        tid = u.GetWindowThreadProcessId(wintypes.HWND(fg), None) if fg else 0
        out["tid"] = int(tid or 0)
        gti = _GUITHREADINFO()
        gti.cbSize = ctypes.sizeof(_GUITHREADINFO)
        # tid=0 asks about the foreground thread; no attach required either way
        if u.GetGUIThreadInfo(wintypes.DWORD(tid or 0), ctypes.byref(gti)):
            out["focus"] = int(gti.hwndFocus or 0)
            out["focus_class"] = _cls(gti.hwndFocus)
            out["caret"] = int(gti.hwndCaret or 0)
            out["caret_xy"] = (gti.rcCaret.left, gti.rcCaret.top)
            out["flags"] = int(gti.flags)
        else:
            out["gti_err"] = int(ctypes.get_last_error() or 0)
    except Exception as exc:      # diagnostics must never break a dictation
        out["error"] = repr(exc)
    return out


def _fmt(s: dict) -> str:
    if not s:
        return "(unavailable)"
    if "error" in s:
        return f"(error {s['error']})"
    parts = [f"fg=0x{s.get('fg', 0):x} {s.get('fg_class', '-')!r}"]
    t = s.get("fg_title")
    if t:
        parts.append(f"'{t}'")
    parts.append(f"tid={s.get('tid', 0)}")
    f = s.get("focus", 0)
    parts.append(f"focus=0x{f:x} {s.get('focus_class', '-')}" if f else "focus=NONE")
    c = s.get("caret", 0)
    if c:
        x, y = s.get("caret_xy", (0, 0))
        parts.append(f"caret=0x{c:x}@({x},{y})")
    else:
        parts.append("caret=NONE")
    return "  ".join(parts)


def focused_hwnd(tid: int):
    """The focus HWND of thread ``tid``, read WITHOUT AttachThreadInput.

    This is the whole point of GetGUIThreadInfo: it answers the same question as
    GetFocus()-while-attached, but without joining input queues. Attaching costs
    the target its caret, which is intolerable on a path that runs while the user
    is mid-sentence.
    """
    if not IS_WINDOWS or not tid:
        return None
    try:
        gti = _GUITHREADINFO()
        gti.cbSize = ctypes.sizeof(_GUITHREADINFO)
        if ctypes.windll.user32.GetGUIThreadInfo(wintypes.DWORD(tid), ctypes.byref(gti)):
            return int(gti.hwndFocus) if gti.hwndFocus else None
    except Exception:
        pass
    return None


def mark(tag: str) -> dict:
    """Log one focus snapshot under ``tag``. No-op unless enabled."""
    if not _enabled:
        return {}
    s = snapshot()
    try:
        log.info("[%s] %s", tag, _fmt(s))
    except Exception:
        pass
    return s
