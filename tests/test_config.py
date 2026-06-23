import pytest

from spokengo import secrets_store
from spokengo.config import Config, load_config, save_config
from spokengo.errors import ConfigError


def test_defaults_are_sane():
    c = Config()
    assert c.provider == "groq"
    assert c.mode == "toggle"
    assert c.sample_rate == 16000


def test_roundtrip(tmp_path):
    p = tmp_path / "config.toml"
    c = Config(mode="push", max_seconds=60, language="ru")
    save_config(c, p)
    loaded = load_config(p)
    assert loaded.mode == "push"
    assert loaded.max_seconds == 60
    assert loaded.language == "ru"


def test_unknown_keys_ignored():
    c = Config.from_dict({"mode": "toggle", "totally_unknown": 5})
    assert c.mode == "toggle"


def test_bad_mode_raises():
    with pytest.raises(ConfigError):
        Config.from_dict({"mode": "nonsense"})


def test_malformed_toml_raises_clear_error(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("this is = = not toml ][", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(p)


def test_missing_file_returns_defaults(tmp_path):
    loaded = load_config(tmp_path / "nope.toml")
    assert loaded.provider == "groq"


def test_secret_roundtrip(monkeypatch):
    # force the env-var backend so the test is hermetic
    monkeypatch.setattr(secrets_store, "_HAVE_KEYRING", False)
    secrets_store.set_key("groq", "sk-test-123")
    assert secrets_store.get_key("groq") == "sk-test-123"
    secrets_store.delete_key("groq")
    assert secrets_store.get_key("groq") is None
