"""Global hotkey manager (Windows). One dedicated thread owns a message loop;
hotkeys are registered/unregistered on THAT thread (RegisterHotKey is thread-
affine) via PostThreadMessage, so the start hotkey can be permanent while the
Enter (stop) / Esc (cancel) hotkeys are added only during recording.

Registration failures NO LONGER crash the thread — they are reported through an
``on_error`` callback so the UI can tell the user the combo is taken.

Pure helpers (parse_combo, combo_from_state) are unit-tested cross-platform.
"""
from __future__ import annotations

import sys
import threading
from typing import Callable, Optional

IS_WINDOWS = sys.platform.startswith("win")

_MODS = {"ctrl": 0x0002, "control": 0x0002, "alt": 0x0001, "shift": 0x0004,
         "win": 0x0008, "super": 0x0008, "cmd": 0x0008}
_NAMED_KEYS = {
    "space": 0x20, "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B,
    "tab": 0x09, "backspace": 0x08, "delete": 0x2E, "insert": 0x2D,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74, "f6": 0x75,
    "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
}


def parse_combo(combo: str):
    """'ctrl+space' -> (modifiers_bitmask, virtual_key_code)."""
    mods, vk = 0, None
    for part in combo.lower().replace(" ", "").split("+"):
        if part in _MODS:
            mods |= _MODS[part]
        elif part in _NAMED_KEYS:
            vk = _NAMED_KEYS[part]
        elif len(part) == 1:
            vk = ord(part.upper())
    if vk is None:
        raise ValueError(f"cannot parse hotkey {combo!r}")
    return mods, vk


# Tkinter event.state bit flags differ slightly per platform; cover Windows.
_TK_SHIFT = 0x0001
_TK_CONTROL = 0x0004
_TK_ALT = 0x20000   # Windows Alt. NB: 0x0008 is NOT Alt on Windows (it gets set
                    # spuriously), which previously glued a phantom "alt" onto
                    # every captured combo.


def combo_from_state(state: int, keysym: str) -> Optional[str]:
    """Build a combo string from a Tk key event's modifier state + keysym.
    Returns None for a bare modifier press (so the capture keeps waiting)."""
    ks = (keysym or "").lower()
    if ks in ("control_l", "control_r", "shift_l", "shift_r", "alt_l", "alt_r",
              "super_l", "super_r", "win_l", "win_r", ""):
        return None
    mods = []
    if state & _TK_CONTROL:
        mods.append("ctrl")
    if state & _TK_ALT:
        mods.append("alt")
    if state & _TK_SHIFT:
        mods.append("shift")
    key = {"return": "enter", "escape": "esc", "prior": "pageup",
           "next": "pagedown"}.get(ks, ks)
    if len(key) == 1:
        key = key.lower()
    return "+".join(mods + [key])


def clipboard_action(keycode: int, state: int):
    """Layout-independent clipboard shortcut resolver. Tk's default Ctrl+C/V/X/A
    bindings match the *symbol* 'v', which never arrives on a non-Latin (e.g.
    Russian) keyboard layout — so paste silently fails. We instead match the
    physical key code (VK code on Windows), which is the same on any layout.
    Returns 'paste' | 'copy' | 'cut' | 'select_all' | None. ``state`` is the Tk
    event modifier mask; bit 0x4 = Control."""
    if not (state & 0x4):
        return None
    return {86: "paste", 67: "copy", 88: "cut", 65: "select_all"}.get(keycode)


if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _WM_HOTKEY = 0x0312
    _WM_APP = 0x8000
    _MSG_REGISTER = _WM_APP + 1
    _MSG_UNREGISTER = _WM_APP + 2
    _MSG_QUIT = _WM_APP + 3
    _MOD_NOREPEAT = 0x4000


class WindowsHotkeyManager:  # pragma: no cover - needs Windows
    def __init__(self):
        self._tid = None
        self._ready = threading.Event()
        self._cbs = {}
        self._combos = {}
        self._pending = {}
        self._next_id = 1
        self._lock = threading.Lock()
        self._on_error: Optional[Callable[[str], None]] = None
        self._thread = None
        self._running = False

    def start(self, on_error: Optional[Callable[[str], None]] = None) -> None:
        self._on_error = on_error
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self._ready.wait(2.0)

    def register(self, combo: str, callback: Callable[[], None]) -> int:
        mods, vk = parse_combo(combo)
        with self._lock:
            hk = self._next_id
            self._next_id += 1
            self._cbs[hk] = callback
            self._combos[hk] = combo
            self._pending[hk] = (mods, vk)
        self._post(_MSG_REGISTER, hk)
        return hk

    def unregister(self, hk_id: int) -> None:
        self._post(_MSG_UNREGISTER, hk_id)

    def stop(self) -> None:
        self._running = False
        self._post(_MSG_QUIT, 0)

    def _post(self, msg, wparam):
        if self._tid:
            ctypes.windll.user32.PostThreadMessageW(self._tid, msg, wparam, 0)

    def _loop(self):
        u = ctypes.windll.user32
        self._tid = ctypes.windll.kernel32.GetCurrentThreadId()
        msg = wintypes.MSG()
        u.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)  # force-create queue
        self._ready.set()
        while self._running and u.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            m = msg.message
            if m == _MSG_REGISTER:
                hk = int(msg.wParam)
                mods, vk = self._pending.get(hk, (None, None))
                if vk is not None and not u.RegisterHotKey(None, hk, mods | _MOD_NOREPEAT, vk):
                    combo = self._combos.get(hk, "?")
                    if self._on_error:
                        self._on_error(combo)
            elif m == _MSG_UNREGISTER:
                hk = int(msg.wParam)
                u.UnregisterHotKey(None, hk)
                self._cbs.pop(hk, None)
                self._combos.pop(hk, None)
            elif m == _MSG_QUIT:
                break
            elif m == _WM_HOTKEY:
                cb = self._cbs.get(int(msg.wParam))
                if cb:
                    cb()
        for hk in list(self._cbs):
            try:
                u.UnregisterHotKey(None, hk)
            except Exception:
                pass


def make_hotkey_manager():
    if IS_WINDOWS:
        return WindowsHotkeyManager()
    raise RuntimeError("global hotkeys require Windows in this MVP")
