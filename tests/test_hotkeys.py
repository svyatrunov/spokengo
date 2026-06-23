import pytest

from spokengo.hotkeys import combo_from_state, parse_combo


def test_parse_basic():
    assert parse_combo("ctrl+space") == (0x0002, 0x20)
    assert parse_combo("enter") == (0, 0x0D)
    assert parse_combo("escape") == (0, 0x1B)


def test_parse_letter_and_mods():
    mods, vk = parse_combo("ctrl+alt+j")
    assert mods == (0x0002 | 0x0001)
    assert vk == ord("J")


def test_parse_invalid():
    with pytest.raises(ValueError):
        parse_combo("ctrl+alt")  # no non-modifier key


# Tk event.state bit flags (Windows): Control=0x4, Shift=0x1, Alt=0x20000
def test_combo_from_state_ctrl_space():
    assert combo_from_state(0x4, "space") == "ctrl+space"


def test_combo_from_state_ctrl_alt_letter():
    assert combo_from_state(0x4 | 0x20000, "j") == "ctrl+alt+j"


def test_combo_from_state_bare_modifier_returns_none():
    assert combo_from_state(0x4, "Control_L") is None


def test_combo_from_state_maps_named_keys():
    assert combo_from_state(0x4, "Return") == "ctrl+enter"
    assert combo_from_state(0, "Escape") == "esc"


# Clipboard shortcuts must work on any keyboard layout (keycode, not keysym).
from spokengo.hotkeys import clipboard_action

CTRL = 0x4

def test_clipboard_paste_by_keycode():
    assert clipboard_action(86, CTRL) == "paste"   # physical V, any layout

def test_clipboard_copy_cut_selectall():
    assert clipboard_action(67, CTRL) == "copy"
    assert clipboard_action(88, CTRL) == "cut"
    assert clipboard_action(65, CTRL) == "select_all"

def test_clipboard_requires_ctrl():
    assert clipboard_action(86, 0) is None         # V without Ctrl -> not paste

def test_clipboard_other_keys_none():
    assert clipboard_action(90, CTRL) is None       # Ctrl+Z not handled here


def test_phantom_alt_bit_0x8_not_added():
    # On Windows the 0x0008 state bit is NOT Alt; it must not glue "alt" on.
    assert combo_from_state(0x4 | 0x8, "space") == "ctrl+space"
    assert combo_from_state(0x8, "k") == "k"
    # real Windows Alt is 0x20000 and must still register
    assert combo_from_state(0x4 | 0x20000, "j") == "ctrl+alt+j"
