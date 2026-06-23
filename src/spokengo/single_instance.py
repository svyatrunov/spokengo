"""Single-instance guard via a named Windows mutex. Prevents two SpokenGo
processes fighting over the same global hotkey (the cause of the 'комбинация
занята' crash and the flaky Ctrl+Space). No-op on non-Windows.
"""
from __future__ import annotations

import sys

IS_WINDOWS = sys.platform.startswith("win")
_handle = None  # keep the mutex alive for the process lifetime


def acquire(name: str = "SpokenGo") -> bool:
    """Return True if we are the first/only instance, False if one is running."""
    global _handle
    if not IS_WINDOWS:
        return True
    import ctypes
    k = ctypes.windll.kernel32
    _handle = k.CreateMutexW(None, False, f"Global\\{name}_singleton")
    ERROR_ALREADY_EXISTS = 183
    return k.GetLastError() != ERROR_ALREADY_EXISTS
