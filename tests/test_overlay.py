"""The recording overlay must appear on start showing the paste target, switch
to 'transcribing', and hide when done — driven purely by the controller, so it's
testable without a display."""
import array
import math
import time

from spokengo.config import Config
from spokengo.controller import GuiController
from spokengo.inject.base import Target
from spokengo.state import State
from spokengo.storage import Storage
from spokengo.transcribe.base import Transcript


def loud(n=16000):
    a = array.array("h", [int(8000 * math.sin(i / 4)) for i in range(n)])
    return a.tobytes()


class FakeOverlay:
    def __init__(self): self.events = []
    def show(self, target=None): self.events.append(("show", target))
    def set_state(self, state, message=""): self.events.append(("state", state))
    def hide(self): self.events.append(("hide", None))


class FakeRecorder:
    def __init__(self, frames): self.frames = frames
    def start(self, on_autostop=None): pass
    def stop(self): return self.frames


class FakeProvider:
    name = "groq"
    def transcribe(self, path, *, model, language=None):
        return Transcript(text="ок")


class FakeInjector:
    def __init__(self, target): self._t = target; self.injected = []
    def capture_target(self): return self._t
    def inject(self, target, text): self.injected.append(text); return True


def _ctrl(tmp_path, overlay, target):
    ctrl = GuiController(
        cfg=Config(debounce_ms=0), root_dir=tmp_path, storage=Storage(tmp_path),
        injector_factory=lambda c: FakeInjector(target),
        recorder_factory=lambda c: FakeRecorder(loud()),
        provider_factory=lambda c: FakeProvider())
    ctrl.overlay = overlay
    return ctrl


def _wait_idle(ctrl):
    for _ in range(200):
        if ctrl.state.state is State.IDLE:
            return
        time.sleep(0.01)


def _wait_event(ov, kind):
    for _ in range(300):
        if any(e[0] == kind for e in ov.events):
            return
        time.sleep(0.01)


def test_overlay_shows_target_then_hides(tmp_path):
    ov = FakeOverlay()
    target = Target(handle=123, title="Telegram", is_app=True, process_name="Telegram.exe")
    ctrl = _ctrl(tmp_path, ov, target)
    ctrl.toggle()                       # start -> overlay.show(target)
    assert ov.events[0][0] == "show"
    assert ov.events[0][1].process_name == "Telegram.exe"
    ctrl.toggle()                       # stop -> transcribe -> hide
    _wait_event(ov, "hide")
    kinds = [e[0] for e in ov.events]
    assert "state" in kinds            # switched to transcribing
    assert kinds[-1] == "hide"         # hidden at the end


def test_overlay_hides_on_silent(tmp_path):
    ov = FakeOverlay()
    ctrl = GuiController(
        cfg=Config(debounce_ms=0), root_dir=tmp_path, storage=Storage(tmp_path),
        injector_factory=lambda c: FakeInjector(None),
        recorder_factory=lambda c: FakeRecorder(b"\x00\x00" * 16000),
        provider_factory=lambda c: FakeProvider())
    ctrl.overlay = ov
    ctrl.toggle(); ctrl.toggle()
    _wait_event(ov, "hide")
    assert ov.events[-1][0] == "hide"   # even a silent take cleans up the overlay


from spokengo.overlay import display_name


def test_display_name_strips_exe():
    assert display_name(Target(handle=1, process_name="Telegram.exe")) == "Telegram"
    assert display_name(Target(handle=1, process_name="Code.exe")) == "Code"


def test_display_name_title_fallback():
    assert display_name(Target(handle=1, title="Untitled - Notepad",
                               process_name="")) == "Untitled - Notepad"[:28]


def test_display_name_none():
    assert display_name(None) == "активное поле"


from spokengo.overlay import render_base, frame_image


def test_render_base_returns_image_and_dot():
    img, dot = render_base("Telegram", "recording", None)
    assert img.size[0] > 100 and img.size[1] > 30
    assert isinstance(dot, tuple) and len(dot) == 2


def test_frame_image_same_size_as_base():
    base, dot = render_base("Code", "recording", None)
    fr = frame_image(base, dot, "recording", 0.4)
    assert fr.size == base.size           # animation never resizes the window
    assert fr.mode == "RGBA"
