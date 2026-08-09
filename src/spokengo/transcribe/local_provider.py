"""Local faster-whisper provider. No API key needed — runs on CPU by default.

Models are auto-detected from the HuggingFace hub cache by scanning for
directories that contain a ctranslate2 model.bin file. The model is
lazy-loaded on first transcription so startup stays fast.

Install dependency:  pip install faster-whisper
Download a model:    huggingface-cli download Systran/faster-whisper-medium
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from ..errors import ConfigError, ProviderError
from .base import Transcript
from .registry import register_provider


def _hf_hub_cache() -> Path:
    if "HUGGINGFACE_HUB_CACHE" in os.environ:
        return Path(os.environ["HUGGINGFACE_HUB_CACHE"])
    if "HF_HOME" in os.environ:
        return Path(os.environ["HF_HOME"]) / "hub"
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / "huggingface" / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def find_local_whisper_models() -> list[str]:
    """Return absolute snapshot paths for cached ctranslate2 faster-whisper models.

    Each path can be passed directly to faster_whisper.WhisperModel().
    """
    hub = _hf_hub_cache()
    if not hub.exists():
        return []
    results = []
    for d in sorted(hub.iterdir()):
        if not d.is_dir() or not d.name.startswith("models--"):
            continue
        snapshots = d / "snapshots"
        if not snapshots.exists():
            continue
        for snap in sorted(snapshots.iterdir(), reverse=True):
            if snap.is_dir() and (snap / "model.bin").exists():
                results.append(str(snap))
                break
    return results


def model_display(path: str) -> str:
    """Human-readable name from a snapshot path.

    e.g. .../models--Systran--faster-whisper-medium/snapshots/abc → Systran/faster-whisper-medium
    """
    p = Path(path)
    folder = p.parent.parent.name  # "models--Systran--faster-whisper-medium"
    parts = folder.replace("models--", "").split("--", 1)
    return "/".join(parts)


def model_size(path: str) -> str:
    """Human-readable size of the model.bin file."""
    model_bin = Path(path) / "model.bin"
    if not model_bin.exists():
        return "?"
    size = model_bin.stat().st_size
    if size >= 1_000_000_000:
        return f"{size / 1_000_000_000:.1f} GB"
    return f"{size / 1_000_000:.0f} MB"


def faster_whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


@register_provider("local")
class LocalWhisperProvider:
    name = "local"

    def __init__(self, model_path: str = ""):
        self._model_path = model_path
        self._model = None

    def _load(self):
        if self._model is not None:
            return self._model
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise ConfigError(
                "faster-whisper не установлен. "
                "Выполните: pip install faster-whisper"
            )
        path = self._model_path
        if not path:
            found = find_local_whisper_models()
            if not found:
                raise ConfigError(
                    "Локальная модель не найдена. "
                    "Скачайте: huggingface-cli download Systran/faster-whisper-medium"
                )
            path = found[0]
        try:
            self._model = WhisperModel(path, device="cpu", compute_type="int8")
        except Exception as exc:
            raise ProviderError(f"Не удалось загрузить модель: {exc}") from exc
        return self._model

    def transcribe(self, audio_path: str, *, model: str = "",
                   language: Optional[str] = None) -> Transcript:
        try:
            m = self._load()
            segments, info = m.transcribe(audio_path, language=language or None)
            text = "".join(s.text for s in segments).strip()
            return Transcript(text=text, language=info.language,
                              duration=info.duration)
        except (ConfigError, ProviderError):
            raise
        except Exception as exc:
            raise ProviderError(f"Локальное распознавание не удалось: {exc}") from exc
