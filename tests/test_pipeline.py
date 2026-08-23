import array
import math

from spokengo.config import Config
from spokengo.errors import BadRequestError, NetworkError
from spokengo.state import State, StateMachine
from spokengo.storage import STATUS_DONE, STATUS_FAILED, STATUS_PENDING, Storage
from spokengo.transcribe.base import Transcript
from spokengo.pipeline import VoicePipeline


def loud(n=16000):
    a = array.array("h", [int(8000 * math.sin(i / 4)) for i in range(n)])
    return a.tobytes()


class FakeProvider:
    name = "groq"
    def __init__(self, text="привет", exc=None):
        self.text, self.exc = text, exc
        self.calls = 0
    def transcribe(self, path, *, model, language=None):
        self.calls += 1
        if self.exc:
            raise self.exc
        return Transcript(text=self.text)


class FakeInjector:
    def __init__(self, ok=True):
        self.ok = ok
        self.injected = []
    def capture_target(self): return None
    def inject(self, target, text):
        self.injected.append(text); return self.ok


def make(tmp_path, provider, injector, notes):
    cfg = Config()
    st = StateMachine(cfg.mode)
    storage = Storage(tmp_path)
    pipe = VoicePipeline(state=st, storage=storage, injector=injector,
                         provider=provider, config=cfg,
                         notify=lambda lvl, msg: notes.append((lvl, msg)))
    st._set(State.RECORDING); st._set(State.TRANSCRIBING)
    return st, storage, pipe


def test_full_success_path(tmp_path):
    notes = []; inj = FakeInjector(ok=True)
    st, storage, pipe = make(tmp_path, FakeProvider("распознано"), inj, notes)
    rid, ok = pipe.process(loud(), target=None)
    assert ok is True
    assert inj.injected == ["распознано"]
    assert storage.get(rid).status == STATUS_DONE
    assert st.state is State.IDLE


def test_silent_recording_skipped(tmp_path):
    notes = []; inj = FakeInjector(); prov = FakeProvider()
    st, storage, pipe = make(tmp_path, prov, inj, notes)
    assert pipe.process(b"\x00\x00" * 16000, target=None) == (None, False)
    assert prov.calls == 0 and inj.injected == []
    assert st.state is State.IDLE


def test_transient_error_is_queued(tmp_path):
    notes = []; inj = FakeInjector()
    prov = FakeProvider(exc=NetworkError("нет сети"))
    st, storage, pipe = make(tmp_path, prov, inj, notes)
    rid, ok = pipe.process(loud(), target=None)
    assert ok is False
    assert storage.get(rid).status == STATUS_PENDING   # saved for manual retry
    assert inj.injected == []
    assert st.state is State.IDLE
    assert any(lvl == "warn" for lvl, _ in notes)


def test_permanent_error_not_queued(tmp_path):
    notes = []; inj = FakeInjector()
    prov = FakeProvider(exc=BadRequestError("неизвестная модель"))
    st, storage, pipe = make(tmp_path, prov, inj, notes)
    rid, ok = pipe.process(loud(), target=None)
    assert rid is None                                  # nothing to retry
    assert storage.pending() == []                      # NOT queued
    assert storage.recent(1)[0].status == STATUS_FAILED
    assert any(lvl == "error" for lvl, _ in notes)
    assert st.state is State.IDLE


def test_injection_failure_keeps_text(tmp_path):
    notes = []; inj = FakeInjector(ok=False)
    st, storage, pipe = make(tmp_path, FakeProvider("text"), inj, notes)
    rid, ok = pipe.process(loud(), target=None)
    assert ok is False                                # injection failed…
    assert storage.get(rid).status == STATUS_DONE     # …but the text is safe
    assert any(lvl == "warn" for lvl, _ in notes)


def test_retry_pending_is_bounded(tmp_path):
    notes = []; inj = FakeInjector()
    # provider always transient-fails -> retries must stop after MAX attempts
    prov = FakeProvider(exc=NetworkError("down"))
    st, storage, pipe = make(tmp_path, prov, inj, notes)
    audio_path = storage.save_audio(b"RIFFfake")
    storage.add("", "groq", "whisper", 1.0, audio_path=audio_path,
                status=STATUS_PENDING)
    total = 0
    for _ in range(10):
        total += pipe.retry_pending(max_attempts=3)
    assert total == 0                                   # never succeeded
    assert storage.pending() == []                      # exhausted -> failed
    assert storage.recent(1)[0].status == STATUS_FAILED


def test_retry_pending_recovers(tmp_path):
    notes = []; inj = FakeInjector()
    prov = FakeProvider("восстановлено")
    st, storage, pipe = make(tmp_path, prov, inj, notes)
    audio_path = storage.save_audio(b"RIFFfake")
    storage.add("", "groq", "whisper", 1.0, audio_path=audio_path,
                status=STATUS_PENDING)
    assert pipe.retry_pending() == 1
    assert storage.pending() == []
