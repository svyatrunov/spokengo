import array
import math

from spokengo.config import Config
from spokengo.controller import GuiController
from spokengo.state import State
from spokengo.storage import STATUS_DONE, Storage
from spokengo.transcribe.base import Transcript


def loud():
    a = array.array("h", [int(8000 * math.sin(i / 4)) for i in range(16000)])
    return a.tobytes()


class FakeRecorder:
    def __init__(self, frames): self.frames = frames; self.started = False
    def start(self, on_autostop=None): self.started = True
    def stop(self): return self.frames


class FakeProvider:
    name = "groq"
    def transcribe(self, path, *, model, language=None):
        return Transcript(text="привет мир")


class FakeInjector:
    def __init__(self): self.injected = []
    def capture_target(self): return None
    def inject(self, target, text): self.injected.append(text); return True


def make(tmp_path, frames=None, provider=None):
    inj = FakeInjector()
    ctrl = GuiController(
        cfg=Config(debounce_ms=0), root_dir=tmp_path, storage=Storage(tmp_path),
        injector_factory=lambda c: inj,
        recorder_factory=lambda c: FakeRecorder(frames if frames is not None else loud()),
        provider_factory=lambda c: provider or FakeProvider())
    return ctrl, inj


def test_toggle_records_then_transcribes_and_injects(tmp_path):
    msgs = []
    ctrl, inj = make(tmp_path)
    ctrl.on_status = lambda st, m: msgs.append((st, m))
    assert ctrl.toggle() == "start"
    assert ctrl.state.state is State.RECORDING
    ctrl.toggle()  # stop -> processes synchronously inside worker
    # worker thread runs _process; join by polling state back to IDLE
    import time
    for _ in range(100):
        if ctrl.state.state is State.IDLE and inj.injected:
            break
        time.sleep(0.01)
    assert inj.injected == ["привет мир"]
    assert ctrl.state.state is State.IDLE


def test_save_settings_persists_config(tmp_path):
    ctrl, _ = make(tmp_path)
    ctrl.save_settings(hotkey="ctrl+alt+d", mode="push", model="whisper-x",
                       language="ru")
    from spokengo.config import load_config
    saved = load_config(tmp_path / "config.toml")
    assert saved.hotkey == "ctrl+alt+d"
    assert saved.mode == "push"
    assert saved.language == "ru"


def test_history_reads_storage(tmp_path):
    ctrl, _ = make(tmp_path)
    ctrl.storage.add("прошлый текст", "groq", "m", 1.0, status=STATUS_DONE)
    rows = ctrl.recent(10)
    assert any("прошлый текст" in r.text for r in rows)


def test_missing_key_does_not_crash(tmp_path):
    def boom(cfg):
        raise RuntimeError("API-ключ для 'groq' не задан")
    msgs = []
    inj = FakeInjector()
    ctrl = GuiController(
        cfg=Config(debounce_ms=0), root_dir=tmp_path, storage=Storage(tmp_path),
        injector_factory=lambda c: inj,
        recorder_factory=lambda c: FakeRecorder(loud()),
        provider_factory=boom)
    ctrl.on_status = lambda st, m: msgs.append(m)
    ctrl.toggle(); ctrl.toggle()
    import time
    for _ in range(100):
        if ctrl.state.state is State.IDLE:
            break
        time.sleep(0.01)
    assert ctrl.state.state is State.IDLE          # recovered, no crash
    assert any("ключ" in m.lower() for m in msgs)  # told the user
