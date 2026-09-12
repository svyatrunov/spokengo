"""Guards for running as a downloaded one-file .exe.

The shipped path for non-technical users is "download one file, double-click".
That mode has no venv beside it and no source tree under it, and the shortcut
logic silently assumed both: it walked past spokengo-gui.exe, past pythonw.exe,
past python.exe, and returned a path that did not exist — a dead Desktop icon.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from spokengo import resources
from spokengo.install import _find_gui_target


@pytest.fixture
def frozen(monkeypatch, tmp_path):
    """Pretend we are a downloaded exe sitting in the user's Downloads."""
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    exe = downloads / "SpokenGo.exe"
    exe.write_bytes(b"MZ fake")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
    return exe


def test_not_frozen_in_a_source_checkout():
    assert resources.is_frozen() is False


def test_resource_dir_follows_the_unpack_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert resources.resource_dir() == tmp_path / "spokengo"


def test_icon_ships_in_the_source_tree():
    assert resources.icon_path().exists(), "the bundled icon must exist to be bundled"


def test_copy_is_a_no_op_outside_a_frozen_build():
    assert resources.ensure_installed_copy() == Path(sys.executable).resolve()


def test_downloaded_exe_installs_itself_somewhere_permanent(frozen, tmp_path):
    dest = resources.ensure_installed_copy()
    assert dest == tmp_path / "LocalAppData" / "Programs" / "SpokenGo" / "SpokenGo.exe"
    assert dest.exists(), "shortcut target must survive the user emptying Downloads"
    assert dest.read_bytes() == b"MZ fake"


def test_second_launch_from_the_installed_copy_does_not_recopy(monkeypatch, tmp_path):
    installed = tmp_path / "LocalAppData" / "Programs" / "SpokenGo" / "SpokenGo.exe"
    installed.parent.mkdir(parents=True)
    installed.write_bytes(b"MZ fake")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(installed))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
    assert resources.ensure_installed_copy() == installed.resolve()


def test_shortcut_points_at_the_installed_copy(frozen, tmp_path):
    resources.ensure_installed_copy()
    target, args = _find_gui_target()
    assert target == str(tmp_path / "LocalAppData" / "Programs" / "SpokenGo" / "SpokenGo.exe")
    assert args == "", "a frozen build launches itself, with no module arguments"


def test_shortcut_never_points_at_a_phantom_path(frozen):
    """The original bug: with no venv beside the exe, the fallbacks ran off the
    end and produced a target that does not exist."""
    target, args = _find_gui_target()
    assert Path(target).exists(), f"dead shortcut target: {target}"
    assert args == ""


def test_source_checkout_behaviour_is_unchanged():
    target, args = _find_gui_target()
    assert Path(target).exists()
    assert target.lower().endswith((".exe",)) or "python" in target.lower()


# --- how the exe is built -------------------------------------------------
# The .exe shipped broken through three tags: PyInstaller was pointed straight
# at src/spokengo/__main__.py, which it runs as top-level __main__ with no
# parent package, so line 7's "from . import __version__" raised
# "attempted relative import with no known parent package" and the app died
# before drawing anything. Nothing caught it because the crash dialog keeps the
# process alive, so "is it still running?" smoke tests reported success.

def _build_script() -> str:
    return (Path(__file__).resolve().parents[1] / "scripts" / "build.ps1").read_text(
        encoding="utf-8")


def test_entry_point_is_outside_the_package():
    build = _build_script()
    assert "pyi_entry.py" in build, "the build must use the out-of-package entry"
    assert r"spokengo\__main__.py" not in build, (
        "pointing PyInstaller inside the package breaks every relative import")


def test_entry_script_imports_the_package_absolutely():
    entry = (Path(__file__).resolve().parents[1] / "scripts" / "pyi_entry.py")
    assert entry.exists()
    src = entry.read_text(encoding="utf-8")
    assert "from spokengo" in src, "must import spokengo as a real package"
    assert "\nfrom ." not in src, "a relative import here reintroduces the bug"


def test_icon_is_bundled_into_the_exe():
    """Without --add-data the .ico is absent at runtime and the window and
    shortcut fall back to a blank icon."""
    assert "--add-data" in _build_script()
