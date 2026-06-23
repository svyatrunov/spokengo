"""API key state: hint, save, clear — hermetic via the env-var backend."""
from spokengo import secrets_store
from spokengo.config import Config
from spokengo.controller import GuiController
from spokengo.storage import Storage


def _ctrl(tmp_path):
    return GuiController(cfg=Config(), root_dir=tmp_path, storage=Storage(tmp_path),
                         injector_factory=lambda c: None,
                         recorder_factory=lambda c: None,
                         provider_factory=lambda c: None)


def test_key_save_hint_clear(tmp_path, monkeypatch):
    monkeypatch.setattr(secrets_store, "_HAVE_KEYRING", False)
    secrets_store.delete_key("groq")
    ctrl = _ctrl(tmp_path)
    assert ctrl.has_key() is False
    assert ctrl.key_hint() == ""
    ctrl.set_api_key("gsk_ABCDEFGH1234WXYZ")
    assert ctrl.has_key() is True
    hint = ctrl.key_hint()
    assert hint.startswith("gsk_") and hint.endswith("WXYZ") and "…" in hint
    ctrl.clear_api_key()
    assert ctrl.has_key() is False


def test_empty_key_does_not_overwrite(tmp_path, monkeypatch):
    monkeypatch.setattr(secrets_store, "_HAVE_KEYRING", False)
    secrets_store.delete_key("groq")
    ctrl = _ctrl(tmp_path)
    ctrl.set_api_key("realkey123456")
    ctrl.set_api_key("   ")          # blank -> keep existing
    assert ctrl.has_key() is True
    secrets_store.delete_key("groq")
