from .base import Transcript, TranscriptionProvider
from .registry import register_provider, get_provider, available_providers
from . import groq_provider  # noqa: F401  (registers "groq")
from . import local_provider  # noqa: F401  (registers "local")
from . import local_engine  # noqa: F401
from .local_provider import (find_local_whisper_models, model_display, model_size,
                             faster_whisper_available, diagnose, LocalStatus)

__all__ = [
    "Transcript", "TranscriptionProvider", "register_provider",
    "get_provider", "available_providers",
    "find_local_whisper_models", "model_display", "model_size", "faster_whisper_available",
    "diagnose", "LocalStatus", "local_engine",
]
