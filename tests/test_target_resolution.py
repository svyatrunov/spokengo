"""'If you didn't click anywhere, the most obvious field': when the active
window is our own/shell, the injector must fall back to the last app window.
Reproduces the resolution logic without Windows."""
from collections import deque

from spokengo.inject.base import Target

_SHELL = {"", "Program Manager", "SpokenGo"}


class FakeInjector:
    def __init__(self):
        self._recent = deque(maxlen=8)
        self._foreground = Target(handle=1, title="Notepad", is_app=True)
    def set_foreground(self, t): self._foreground = t
    def capture_target(self):
        cur = self._foreground
        if cur.is_app:
            self._recent.appendleft(cur)
            return cur
        for t in self._recent:
            if t.is_app:
                return t
        return cur


def test_uses_active_app_window():
    inj = FakeInjector()
    t = inj.capture_target()
    assert t.title == "Notepad"


def test_falls_back_to_last_app_when_shell_active():
    inj = FakeInjector()
    inj.capture_target()  # Notepad recorded
    inj.set_foreground(Target(handle=0, title="SpokenGo", is_app=False))
    t = inj.capture_target()
    assert t.title == "Notepad"  # not our own window
