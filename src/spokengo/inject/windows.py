"""Windows implementation of focus capture + text injection.

Imports win32 lazily, so the package imports on any OS (and CI). The platform
risk lives here; everything above it is OS-independent and tested.

Strategy:
  * capture_target(): remember the foreground HWND at hotkey time, plus a short
    history of recent *application* windows. If the active window is our own /
    shell / desktop, fall back to the last real app window ("if you didn't
    click anywhere, the most obvious field").
  * inject(): SetForegroundWindow(target) -> clipboard paste (Ctrl+V). If paste
    fails, fall back to char-by-char unicode SendInput.
"""
from __future__ import annotations

import sys
import time
from collections import deque
from typing import Deque, Optional

from .base import ClipboardBackend, Target
from .clipboard import ClipboardCycle

IS_WINDOWS = sys.platform.startswith("win")

# Window titles that are not real input targets.
_SHELL_TITLES = {"", "Program Manager", "SpokenGo"}


class Win32Clipboard(ClipboardBackend):  # pragma: no cover - needs Windows
    def get_text(self) -> Optional[str]:
        import win32clipboard as cb
        cb.OpenClipboard()
        try:
            import win32con
            if cb.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                return cb.GetClipboardData(win32con.CF_UNICODETEXT)
            return None
        finally:
            cb.CloseClipboard()

    def set_text(self, text: str) -> None:
        import win32clipboard as cb
        import win32con
        cb.OpenClipboard()
        try:
            cb.EmptyClipboard()
            cb.SetClipboardData(win32con.CF_UNICODETEXT, text)
        finally:
            cb.CloseClipboard()


class WindowsInjector:  # pragma: no cover - needs Windows
    def __init__(self, fallback_typing: bool = True):
        self.fallback_typing = fallback_typing
        self._recent_apps: Deque[Target] = deque(maxlen=8)
        self._clip = Win32Clipboard()
        self._cycle = ClipboardCycle(self._clip, restore_delay=0.5)

    # --- focus ---------------------------------------------------------
    def _foreground(self) -> Target:
        import win32gui
        from .win_icon import get_process_name
        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd) if hwnd else ""
        is_app = title not in _SHELL_TITLES
        proc = get_process_name(hwnd) if hwnd else ""
        return Target(handle=hwnd or None, title=title, is_app=is_app,
                      process_name=proc)

    def capture_target(self) -> Target:
        cur = self._foreground()
        if cur.is_app:
            # avoid duplicate consecutive entries (live overlay polls often)
            if not self._recent_apps or self._recent_apps[0].handle != cur.handle:
                self._recent_apps.appendleft(cur)
            return cur
        # Active window is shell/our own -> use last real app window (bug #3).
        for t in self._recent_apps:
            if t.is_app:
                return t
        return cur

    def _focus(self, target: Target) -> None:
        """Reliably bring the captured target window to the foreground before
        pasting. Plain SetForegroundWindow often fails across processes due to
        Windows' foreground lock, so we AttachThreadInput to the current
        foreground thread first, then wait until the target is actually focused
        (a cross-window switch needs time to settle before we send Ctrl+V)."""
        if not target.handle:
            return
        import ctypes
        import win32con
        import win32gui
        import win32process
        hwnd = target.handle
        user32 = ctypes.windll.user32
        try:
            fg = win32gui.GetForegroundWindow()
            cur_tid = win32process.GetWindowThreadProcessId(fg)[0] if fg else 0
            tgt_tid = win32process.GetWindowThreadProcessId(hwnd)[0]
            attached = False
            if cur_tid and cur_tid != tgt_tid:
                attached = bool(user32.AttachThreadInput(cur_tid, tgt_tid, True))
            try:
                if win32gui.IsIconic(hwnd):
                    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                win32gui.BringWindowToTop(hwnd)
                win32gui.SetForegroundWindow(hwnd)
            except Exception:
                pass
            if attached:
                user32.AttachThreadInput(cur_tid, tgt_tid, False)
        except Exception:
            pass
        # wait until the target is really the foreground window
        for _ in range(25):
            try:
                if win32gui.GetForegroundWindow() == hwnd:
                    break
            except Exception:
                break
            time.sleep(0.02)
        time.sleep(0.06)  # let focus + the focused control settle

    # --- injection -----------------------------------------------------
    def _send_paste(self) -> None:
        import win32api
        import win32con
        VK_CONTROL, VK_V = win32con.VK_CONTROL, 0x56
        win32api.keybd_event(VK_CONTROL, 0, 0, 0)
        win32api.keybd_event(VK_V, 0, 0, 0)
        time.sleep(0.02)
        win32api.keybd_event(VK_V, 0, win32con.KEYEVENTF_KEYUP, 0)
        win32api.keybd_event(VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(0.06)  # let the target begin consuming the paste

    def type_text(self, text: str) -> None:
        """Unicode char-by-char fallback via SendInput (handles Cyrillic, bug #24)."""
        import ctypes
        from ctypes import wintypes

        KEYEVENTF_UNICODE = 0x0004
        KEYEVENTF_KEYUP = 0x0002
        INPUT_KEYBOARD = 1

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]

        class _U(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("u", _U)]

        send = ctypes.windll.user32.SendInput
        for ch in text:
            for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
                inp = INPUT(type=INPUT_KEYBOARD,
                            u=_U(ki=KEYBDINPUT(0, ord(ch), flags, 0, None)))
                send(1, ctypes.byref(inp), ctypes.sizeof(inp))

    def inject(self, target: Target, text: str) -> bool:
        if not text:
            return False
        self._focus(target)
        try:
            return self._cycle.paste(text, self._send_paste)
        except Exception:
            if self.fallback_typing:
                self.type_text(text)
                return True
            return False


def make_injector(fallback_typing: bool = True):
    if IS_WINDOWS:
        return WindowsInjector(fallback_typing=fallback_typing)
    raise RuntimeError("WindowsInjector requires Windows; use a fake in tests")
