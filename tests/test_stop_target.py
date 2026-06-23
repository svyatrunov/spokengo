"""The paste target must be the window active at STOP time (where the user is
now), and that's also what the overlay should reflect."""
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


class SeqInjector:
    """capture_target returns window 'A' first (start), 'B' second (stop)."""
    def __init__(self):
        self._seq = [Target(handle=1, title="A", process_name="A.exe"),
                     Target(handle=2, title="B", process_name="B.exe")]
        self._i = 0
        self.injected_targets = []
    def capture_target(self):
        t = self._seq[min(self._i, len(self._seq) - 1)]
        self._i += 1
        return t
    def inject(self, target, text):
        self.injected_targets.append(target)
        return True


class FakeRecorder:
    def __init__(self, frames): self.frames = frames
    def start(self, on_autostop=None): pass
    def stop(self): return self.frames


class FakeProvider:
    name = "groq"
    def transcribe(self, path, *, model, language=None):
        return Transcript(text="привет")


def test_paste_target_is_stop_time_window(tmp_path):
    inj = SeqInjector()
    ctrl = GuiController(
        cfg=Config(debounce_ms=0), root_dir=tmp_path, storage=Storage(tmp_path),
        injector_factory=lambda c: inj,
        recorder_factory=lambda c: FakeRecorder(loud()),
        provider_factory=lambda c: FakeProvider())
    ctrl.start_recording()                 # capture #1 -> A (overlay/start)
    ctrl.stop_recording()                  # capture #2 -> B (paste target)
    for _ in range(200):
        if ctrl.state.state is State.IDLE and inj.injected_targets:
            break
        time.sleep(0.01)
    assert len(inj.injected_targets) == 1
    assert inj.injected_targets[0].title == "B"   # pasted where user ended up


def test_peek_target_returns_foreground(tmp_path):
    inj = SeqInjector()
    ctrl = GuiController(
        cfg=Config(), root_dir=tmp_path, storage=Storage(tmp_path),
        injector_factory=lambda c: inj,
        recorder_factory=lambda c: FakeRecorder(loud()),
        provider_factory=lambda c: FakeProvider())
    assert ctrl.peek_target().title == "A"
