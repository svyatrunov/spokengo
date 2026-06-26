import array
import math

from spokengo.config import Config
from spokengo.controller import GuiController
from spokengo.storage import STATUS_DONE, STATUS_FAILED, STATUS_PENDING, Storage
from spokengo.transcribe.base import Transcript


class FakeProvider:
    name = "groq"
    def __init__(self, text="восстановлено", exc=None):
        self.text, self.exc = text, exc
    def transcribe(self, path, *, model, language=None):
        if self.exc:
            raise self.exc
        return Transcript(text=self.text)


def _ctrl(tmp_path, provider):
    return GuiController(
        cfg=Config(), root_dir=tmp_path, storage=Storage(tmp_path),
        injector_factory=lambda c: None, recorder_factory=lambda c: None,
        provider_factory=lambda c: provider)


def test_retry_one_recovers_queued(tmp_path):
    st = Storage(tmp_path)
    audio = st.save_audio(b"RIFFfake")
    rid = st.add("", "groq", "whisper", 1.0, audio_path=audio, status=STATUS_PENDING)
    st.close()
    ctrl = _ctrl(tmp_path, FakeProvider("готовый текст"))
    assert ctrl.retry_one(rid) is True
    assert ctrl.storage.get(rid).text == "готовый текст"
    assert ctrl.storage.get(rid).status == STATUS_DONE


def test_retry_one_works_on_failed_item(tmp_path):
    st = Storage(tmp_path)
    audio = st.save_audio(b"RIFFfake")
    rid = st.add("", "groq", "whisper", 1.0, audio_path=audio, status=STATUS_FAILED)
    st.close()
    ctrl = _ctrl(tmp_path, FakeProvider("со второго раза"))
    assert ctrl.retry_one(rid) is True   # explicit retry resets a failed item
    assert ctrl.storage.get(rid).text == "со второго раза"


def test_last_text_returns_newest_nonempty(tmp_path):
    ctrl = _ctrl(tmp_path, FakeProvider())
    ctrl.storage.add("первый", "groq", "m", 1.0, status=STATUS_DONE, ts=100)
    ctrl.storage.add("", "groq", "m", 1.0, status=STATUS_PENDING, ts=200)  # newer, empty
    ctrl.storage.add("последний", "groq", "m", 1.0, status=STATUS_DONE, ts=300)
    assert ctrl.last_text() == "последний"
