"""`spokengo transcribe <file>` — a one-off re-run of an existing recording
through any registered provider, bypassing the recorder/history/paste chain.
Handy over the CLI when a dictation got stuck or mistranscribed and the user
wants to try the same audio through a different provider (e.g. local -> groq).
"""
from __future__ import annotations

from spokengo.__main__ import main
from spokengo.errors import AuthError
from spokengo.transcribe.base import Transcript
from spokengo.transcribe.registry import register_provider


@register_provider("faketest")
class _FakeProvider:
    name = "faketest"
    last_call = {}

    def __init__(self, api_key=None):
        self.api_key = api_key

    def transcribe(self, audio_path, *, model="", language=None):
        _FakeProvider.last_call = {"audio_path": audio_path, "model": model, "language": language}
        return Transcript(text="привет из теста")


def _wav(tmp_path, name="clip.wav"):
    p = tmp_path / name
    p.write_bytes(b"RIFF....WAVEfmt ")
    return p


def test_transcribe_missing_file_returns_1(tmp_path, capsys):
    rc = main(["transcribe", str(tmp_path / "nope.wav"), "--provider", "faketest"])
    assert rc == 1
    assert "не найден" in capsys.readouterr().out


def test_transcribe_prints_text_and_exits_0(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    from spokengo import secrets_store
    monkeypatch.setattr(secrets_store, "get_key", lambda provider: "fakekey")
    wav = _wav(tmp_path)
    rc = main(["transcribe", str(wav), "--provider", "faketest", "--language", "ru"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.strip() == "привет из теста"
    assert _FakeProvider.last_call["language"] == "ru"
    assert _FakeProvider.last_call["audio_path"] == str(wav)


def test_transcribe_missing_key_returns_1(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    from spokengo import secrets_store
    monkeypatch.setattr(secrets_store, "get_key", lambda provider: None)
    wav = _wav(tmp_path)
    rc = main(["transcribe", str(wav), "--provider", "groq"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "ключ" in out
