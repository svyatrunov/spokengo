import pytest

from spokengo.errors import ConfigError
from spokengo.transcribe import available_providers, get_provider
from spokengo.transcribe.registry import register_provider


def test_groq_registered():
    assert "groq" in available_providers()
    cls = get_provider("groq")
    assert cls.name == "groq"


def test_unknown_provider_raises():
    with pytest.raises(ConfigError):
        get_provider("does-not-exist")


def test_can_register_new_provider():
    @register_provider("dummy")
    class Dummy:
        def transcribe(self, p, *, model, language=None):
            return None
    assert "dummy" in available_providers()
    assert get_provider("dummy").name == "dummy"
