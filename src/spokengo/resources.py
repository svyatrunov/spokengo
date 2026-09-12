"""Locating bundled files and the app's own executable.

A PyInstaller one-file build unpacks its data into a temporary directory and
sets ``sys._MEIPASS`` to it, so paths derived from ``__file__`` — correct in a
source checkout — point into a folder that disappears when the app exits.
Everything that needs a bundled file goes through here instead.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def is_frozen() -> bool:
    """True when running from a built .exe rather than a source checkout."""
    return bool(getattr(sys, "frozen", False))


def resource_dir() -> Path:
    """Directory that holds bundled data files, in either mode."""
    base = getattr(sys, "_MEIPASS", None)
    return (Path(base) / "spokengo") if base else Path(__file__).parent


def icon_path() -> Path:
    return resource_dir() / "assets" / "spokengo.ico"


def installed_exe_path() -> Path:
    """Where a downloaded one-file exe should live permanently."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "Programs" / "SpokenGo" / "SpokenGo.exe"


def ensure_installed_copy() -> Path:
    """Put a downloaded exe somewhere permanent and return that path.

    Someone who runs the download straight from their Downloads folder would
    otherwise get a Desktop shortcut into that folder — dead the moment they
    tidy it up. Copying on first launch keeps the shortcut valid without asking
    them to do anything.

    No-op outside a frozen build. On any failure the running executable is
    returned, so a caller always receives a path that really exists.
    """
    cur = Path(sys.executable).resolve()
    if not is_frozen():
        return cur
    target = installed_exe_path()
    try:
        if target.exists() and target.samefile(cur):
            return cur                      # already running the installed copy
    except OSError:
        pass
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cur, target)
        return target
    except OSError:
        return cur                          # locked or no permission — harmless
