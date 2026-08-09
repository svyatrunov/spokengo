"""Desktop/Start Menu shortcut installer for Windows.

Uses win32com (already a runtime dep) to create .lnk shortcuts.
Supports custom icons via Pillow conversion (PNG/JPG → .ico).

The icon is always copied to the config dir so the shortcut survives
venv rebuilds and Python upgrades.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Optional


def _scripts_dir() -> Path:
    return Path(sys.executable).parent


def _find_gui_target() -> tuple[str, str]:
    """Return (executable, arguments) for the SpokenGo GUI shortcut.

    Prefers the console-less spokengo-gui.exe entry point. Falls back to
    pythonw.exe -m spokengo gui so the shortcut works from a dev checkout too.
    """
    scripts = _scripts_dir()
    exe = scripts / "spokengo-gui.exe"
    if exe.exists():
        return str(exe), ""
    pythonw = scripts / "pythonw.exe"
    if not pythonw.exists():
        pythonw = scripts / "python.exe"
    return str(pythonw), "-m spokengo gui"


def _bundled_ico() -> Path:
    return Path(__file__).parent / "assets" / "spokengo.ico"


def make_ico(src: str | Path, dst: str | Path) -> None:
    """Convert any Pillow-readable image to a multi-size .ico file."""
    from PIL import Image
    img = Image.open(src).convert("RGBA")
    sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
    # Resize to each size with high-quality resampling
    imgs = [img.resize(s, Image.LANCZOS) for s in sizes]
    imgs[0].save(str(dst), format="ICO",
                 append_images=imgs[1:],
                 sizes=sizes)


def _ensure_icon(custom_src: Optional[str | Path], config_dir: Path) -> str:
    """Return the path to the .ico that the shortcut should reference.

    Copies the icon to config_dir so it lives at a stable, non-venv path.
    If a custom source image is provided it is converted; otherwise the
    bundled icon is used.

    Uses write-to-temp-then-replace so the operation is safe even when
    Explorer has the existing icon.ico locked (e.g., right after a previous
    shortcut was created).
    """
    import os
    import tempfile

    config_dir.mkdir(parents=True, exist_ok=True)
    dst = config_dir / "icon.ico"

    if custom_src is not None:
        src = Path(custom_src)
        # Write to a temp file in the same dir, then atomically replace.
        fd, tmp_path = tempfile.mkstemp(suffix=".ico", dir=config_dir)
        os.close(fd)
        tmp = Path(tmp_path)
        try:
            if src.suffix.lower() == ".ico":
                shutil.copy2(src, tmp)
            else:
                make_ico(src, tmp)
            try:
                os.replace(tmp, dst)
                return str(dst)
            except OSError:
                # dst is locked (Explorer caching it); use the temp file as-is
                return str(tmp)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
    elif not dst.exists():
        bundled = _bundled_ico()
        if bundled.exists():
            shutil.copy2(bundled, dst)

    if dst.exists():
        return str(dst)
    bundled = _bundled_ico()
    return str(bundled) if bundled.exists() else ""


def create_shortcut(
    custom_icon: Optional[str | Path] = None,
    *,
    config_dir: Optional[Path] = None,
    desktop: bool = True,
    start_menu: bool = False,
) -> list[Path]:
    """Create SpokenGo shortcuts and return their paths.

    Args:
        custom_icon: Path to any image file (PNG, JPG, ICO, …). When omitted
                     the bundled spokengo.ico is used.
        config_dir:  Where to persist the icon copy. Defaults to the app's
                     config dir (~/.config/SpokenGo or %APPDATA%/SpokenGo).
        desktop:     Create a shortcut on the Desktop.
        start_menu:  Create a shortcut in Start Menu → SpokenGo.
    """
    import win32com.client  # pywin32 — already in runtime deps

    if config_dir is None:
        from .config import default_config_dir
        config_dir = default_config_dir()

    icon_path = _ensure_icon(custom_icon, config_dir)
    target, arguments = _find_gui_target()

    shell = win32com.client.Dispatch("WScript.Shell")
    locations: list[Path] = []

    if desktop:
        desk = Path(shell.SpecialFolders("Desktop"))
        locations.append(desk / "SpokenGo.lnk")

    if start_menu:
        sm = Path(shell.SpecialFolders("Programs")) / "SpokenGo"
        sm.mkdir(parents=True, exist_ok=True)
        locations.append(sm / "SpokenGo.lnk")

    for lnk in locations:
        sc = shell.CreateShortCut(str(lnk))
        sc.Targetpath = target
        if arguments:
            sc.Arguments = arguments
        sc.WorkingDirectory = str(Path.home())
        sc.Description = "SpokenGo — голосовой ввод"
        if icon_path:
            sc.IconLocation = icon_path
        try:
            sc.ApplicationID = "wind.slw.SpokenGo"
        except Exception:
            pass
        sc.save()

    return locations


_SENTINEL = ".shortcut_created"


def maybe_create_shortcut_once(config_dir: Optional[Path] = None) -> None:
    """Silently create the desktop shortcut on first GUI launch. Never raises."""
    try:
        if config_dir is None:
            from .config import default_config_dir
            config_dir = default_config_dir()
        sentinel = config_dir / _SENTINEL
        if sentinel.exists():
            return
        create_shortcut(config_dir=config_dir)
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.touch()
    except Exception:
        pass  # never block the app from starting
