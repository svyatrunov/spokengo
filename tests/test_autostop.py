"""The request-flood regression: a long recording must trigger exactly one stop
and exactly one transcription, no matter how many times the audio callback fires
after the duration limit."""
import array
import math
import time

from spokengo.audio import AudioRecorder
from spokengo.config import Config
from spokengo.controller import GuiController
from spokengo.state import State
from spokengo.storage import Storage
from spokengo.transcribe.base import Transcript


def loud(n):
    a = array.array("h", [int(8000 * math.sin(i / 4)) for i in range(n)])
    return a.tobytes()


def test_recorder_autostop_fires_once():
    # 1 second limit at 16k; feed 2s of audio in 0.5s blocks => limit crossed,
    # callback keeps coming, but on_autostop must fire exactly once.
    rec = AudioRecorder(sample_rate=16000, max_seconds=1)
    fired = {"n": 0}
    rec._on_autostop = lambda: fired.__setitem__("n", fired["n"] + 1)
    block = loud(8000)  # 0.5s
    for _ in range(6):  # 3 seconds total, well past the 1s limit
        rec._callback(block, len(block) // 2, None, None)
    assert fired["n"] == 1


class CapturingRecorder:
    """Records the on_autostop callback so the test can fire it many times."""
    def __init__(self, frames):
        self.frames = frames
        self.autostop = None
        self.stops = 0
    def start(self, on_autostop=None):
        self.autostop = on_autostop
    def stop(self):
        self.stops += 1
        return self.frames


class FakeInjector:
    def __init__(self): self.injected = []
    def capture_target(self): return None
    def inject(self, target, text): self.injected.append(text); return True


class FakeProvider:
    name = "groq"
    def __init__(self): self.calls = 0
    def transcribe(self, path, *, model, language=None):
        self.calls += 1
        return Transcript(text="одно распознавание")


def test_controller_autostop_processes_once(tmp_path):
    inj = FakeInjector(); prov = FakeProvider()
    rec = CapturingRecorder(loud(16000))
    ctrl = GuiController(
        cfg=Config(debounce_ms=0), root_dir=tmp_path, storage=Storage(tmp_path),
        injector_factory=lambda c: inj, recorder_factory=lambda c: rec,
        provider_factory=lambda c: prov)
    ctrl.toggle()                       # start recording
    assert ctrl.state.state is State.RECORDING
    # simulate the audio thread firing autostop repeatedly (the old flood)
    for _ in range(20):
        rec.autostop()
    for _ in range(200):
        if ctrl.state.state is State.IDLE and inj.injected:
            break
        time.sleep(0.01)
    assert prov.calls == 1              # ONE API call, not twenty
    assert inj.injected == ["одно распознавание"]
    assert rec.stops == 1               # recorder stopped once
    assert ctrl.state.state is State.IDLE
