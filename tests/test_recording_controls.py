"""Start / stop (Enter) / cancel (Esc) split, plus the recording-scoped hotkey
callbacks the GUI uses to register Enter/Esc only while recording."""
import array
import math
import time

from spokengo.config import Config
from spokengo.controller import GuiController
from spokengo.state import State
from spokengo.storage import Storage
from spokengo.transcribe.base import Transcript


def loud(n=16000):
    a = array.array("h", [int(8000 * math.sin(i / 4)) for i in range(n)])
    return a.tobytes()


class FakeRecorder:
    def __init__(self, frames): self.frames = frames; self.stops = 0
    def start(self, on_autostop=None): self.autostop = on_autostop
    def stop(self): self.stops += 1; return self.frames


class FakeProvider:
    name = "groq"
    def __init__(self): self.calls = 0
    def transcribe(self, path, *, model, language=None):
        self.calls += 1
        return Transcript(text="готово")


class FakeInjector:
    def __init__(self): self.injected = []
    def capture_target(self): return None
    def inject(self, target, text): self.injected.append(text); return True


def _ctrl(tmp_path, rec, prov, inj):
    return GuiController(
        cfg=Config(debounce_ms=0), root_dir=tmp_path, storage=Storage(tmp_path),
        injector_factory=lambda c: inj, recorder_factory=lambda c: rec,
        provider_factory=lambda c: prov)


def _wait_idle(ctrl):
    for _ in range(200):
        if ctrl.state.state is State.IDLE:
            return
        time.sleep(0.01)


def test_start_then_stop(tmp_path):
    rec = FakeRecorder(loud()); prov = FakeProvider(); inj = FakeInjector()
    started, stopped = [], []
    ctrl = _ctrl(tmp_path, rec, prov, inj)
    ctrl.on_recording_started = lambda: started.append(1)
    ctrl.on_recording_stopped = lambda: stopped.append(1)
    assert ctrl.start_recording() is True
    assert ctrl.state.state is State.RECORDING
    assert started == [1]                 # GUI would register Enter/Esc here
    ctrl.stop_recording()
    _wait_idle(ctrl)
    assert stopped == [1]                 # GUI would unregister Enter/Esc
    assert inj.injected == ["готово"]


def test_cancel_discards_without_transcribing(tmp_path):
    rec = FakeRecorder(loud()); prov = FakeProvider(); inj = FakeInjector()
    stopped = []
    ctrl = _ctrl(tmp_path, rec, prov, inj)
    ctrl.on_recording_stopped = lambda: stopped.append(1)
    ctrl.start_recording()
    ctrl.cancel_recording()
    assert ctrl.state.state is State.IDLE
    assert prov.calls == 0                # never transcribed
    assert inj.injected == []
    assert rec.stops == 1                 # recorder was stopped
    assert stopped == [1]                 # Enter/Esc unregistered


def test_start_only_when_idle(tmp_path):
    rec = FakeRecorder(loud()); prov = FakeProvider(); inj = FakeInjector()
    ctrl = _ctrl(tmp_path, rec, prov, inj)
    assert ctrl.start_recording() is True
    assert ctrl.start_recording() is False   # already recording -> ignored
