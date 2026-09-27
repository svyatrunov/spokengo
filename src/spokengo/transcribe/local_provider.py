"""Local (offline) provider with two interchangeable backends.

* **whisper.cpp** (``local_engine``) — the default. A tiny native CLI the app
  downloads itself plus a GGML model file. Works from the frozen .exe.
* **faster-whisper** — used only when the library is importable (source
  install) and a CTranslate2 model directory is selected.

The backend is chosen per model: a ``.bin`` GGML file runs through
whisper.cpp, a snapshot *directory* through faster-whisper. ``diagnose()``
tells the UI exactly what is missing so the user never sees a dead end.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from ..errors import ConfigError, ProviderError
from . import local_engine as eng
from .base import Transcript
from .registry import register_provider


def faster_whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Discovery / diagnostics used by the UI and by the provider itself
# ---------------------------------------------------------------------------
def find_all_models(extra_dirs=(), selected: str = "") -> List[eng.LocalModel]:
    """Every usable-or-nearly-usable model on the machine, GGML first."""
    models = eng.find_ggml_models(extra_dirs) + eng.find_ct2_models(extra_dirs)
    if selected:
        picked = eng.classify_model_path(selected)
        if picked and all(Path(m.path) != Path(picked.path) for m in models):
            models.insert(0, picked)
    return models


def model_is_runnable(m: eng.LocalModel, engine: Optional[Path], fw_ok: bool) -> bool:
    return (engine is not None) if m.kind == "ggml" else fw_ok


@dataclass
class LocalStatus:
    engine: Optional[Path]
    faster_whisper: bool
    models: List[eng.LocalModel] = field(default_factory=list)
    selected: Optional[eng.LocalModel] = None

    @property
    def ready(self) -> bool:
        return self.selected is not None and model_is_runnable(
            self.selected, self.engine, self.faster_whisper)

    @property
    def runnable_models(self) -> List[eng.LocalModel]:
        return [m for m in self.models
                if model_is_runnable(m, self.engine, self.faster_whisper)]

    def headline(self) -> str:
        """One line for the status area — what works or what to do next."""
        if self.ready:
            via = "whisper.cpp" if self.selected.kind == "ggml" else "faster-whisper"
            slow = " · медленно на CPU" if self.selected.slow else ""
            return f"Готово: {self.selected.name} · {self.selected.size_label}{slow} · {via}"
        if self.engine is None and not self.faster_whisper:
            return "Нужен движок — установится одной кнопкой (~8 МБ)"
        if not self.models:
            return "Движок на месте, осталось скачать модель"
        if self.selected is None:
            return "Выберите модель из списка"
        if self.selected.kind == "ct2":
            return ("Эта модель для faster-whisper, а библиотека не установлена — "
                    "скачайте GGML-модель ниже")
        return "Для этой модели нужен движок whisper.cpp — установите его"


def diagnose(*, local_model: str = "", extra_dirs=()) -> LocalStatus:
    engine = eng.find_engine(extra_dirs)
    fw = faster_whisper_available()
    models = find_all_models(extra_dirs, local_model)
    selected = None
    if local_model:
        for m in models:
            if Path(m.path) == Path(local_model):
                selected = m
                break
    if selected is None:
        selected = pick_default(models, engine, fw)
    return LocalStatus(engine=engine, faster_whisper=fw, models=models, selected=selected)


def pick_default(models: List[eng.LocalModel], engine: Optional[Path],
                 fw_ok: bool) -> Optional[eng.LocalModel]:
    """Best model that can actually run right now; prefers the catalog's
    recommended one, then the biggest GGML that is not slow on a CPU, then any
    CT2, and a slow (large) model only when nothing else is there."""
    runnable = [m for m in models if model_is_runnable(m, engine, fw_ok)]
    if not runnable:
        return models[0] if models else None
    for m in runnable:
        if m.spec and m.spec.recommended:
            return m
    ranked = sorted(runnable, key=lambda m: (m.slow, m.kind != "ggml", -m.size_bytes))
    return ranked[0]


# Backwards-compatible helpers (older UI/tests import these names)
def find_local_whisper_models() -> list[str]:
    return [m.path for m in eng.find_ct2_models()]


def model_display(path: str) -> str:
    m = eng.classify_model_path(path)
    if m:
        return m.name
    p = Path(path)
    folder = p.parent.parent.name
    if folder.startswith("models--"):
        return "/".join(folder.replace("models--", "").split("--", 1))
    return p.name


def model_size(path: str) -> str:
    m = eng.classify_model_path(path)
    return m.size_label if m else "?"


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------
@register_provider("local")
class LocalWhisperProvider:
    name = "local"

    def __init__(self, model_path: str = "", *, extra_dirs=(), threads: int = 0,
                 engine_path: Optional[str] = None):
        self._model_path = model_path
        self._extra_dirs = tuple(extra_dirs or ())
        self._threads = threads
        self._engine_path = Path(engine_path) if engine_path else None
        self._resolved: Optional[eng.LocalModel] = None
        self._fw_model = None

    # -- resolution ---------------------------------------------------------
    def _resolve(self) -> eng.LocalModel:
        if self._resolved is not None:
            return self._resolved
        if self._model_path:
            m = eng.classify_model_path(self._model_path)
            if m is None:
                raise ConfigError(
                    "Выбранная локальная модель не найдена на диске — откройте "
                    "Настройки → Локально и выберите другую.")
        else:
            st = diagnose(extra_dirs=self._extra_dirs)
            if not st.ready:
                raise ConfigError("Локальный режим не настроен: " + st.headline())
            m = st.selected
        self._resolved = m
        return m

    def _engine(self) -> Path:
        exe = self._engine_path or eng.find_engine(self._extra_dirs)
        if exe is None:
            raise ConfigError(
                "Движок whisper.cpp не установлен — нажмите «Установить движок» "
                "в Настройках → Локально.")
        return exe

    # -- backends -----------------------------------------------------------
    def _transcribe_ggml(self, m: eng.LocalModel, audio_path: str,
                         language: Optional[str]) -> Transcript:
        text = eng.run_engine(self._engine(), Path(m.path), Path(audio_path),
                              language=language, threads=self._threads)
        return Transcript(text=text, language=language)

    def _transcribe_ct2(self, m: eng.LocalModel, audio_path: str,
                        language: Optional[str]) -> Transcript:
        if self._fw_model is None:
            try:
                from faster_whisper import WhisperModel
            except Exception:
                raise ConfigError(
                    "Эта модель требует faster-whisper, которого нет в этой сборке. "
                    "Скачайте GGML-модель в Настройках → Локально.")
            try:
                self._fw_model = WhisperModel(m.path, device="cpu", compute_type="int8")
            except Exception as exc:
                raise ProviderError(f"Не удалось загрузить модель: {exc}") from exc
        segments, info = self._fw_model.transcribe(audio_path, language=language or None)
        text = "".join(s.text for s in segments).strip()
        return Transcript(text=text, language=getattr(info, "language", None),
                          duration=getattr(info, "duration", None))

    # -- public -------------------------------------------------------------
    def transcribe(self, audio_path: str, *, model: str = "",
                   language: Optional[str] = None) -> Transcript:
        try:
            m = self._resolve()
            if m.kind == "ggml":
                return self._transcribe_ggml(m, audio_path, language)
            return self._transcribe_ct2(m, audio_path, language)
        except ProviderError:
            raise
        except ConfigError as exc:
            # Permanent (not queued for auto-retry) but the audio is kept in
            # History, so the user can set things up and press «Повторить».
            raise ProviderError(str(exc)) from exc
        except Exception as exc:
            raise ProviderError(f"Локальное распознавание не удалось: {exc}") from exc
