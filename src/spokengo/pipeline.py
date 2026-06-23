"""Glue: recorded audio -> transcript -> inject -> persist. Degradation rules:
  * permanent provider error (bad key, bad model, file too large) -> notify the
    user, mark FAILED, NEVER retry. This is what stops runaway request loops.
  * transient error (429 / 5xx / network) -> queue as PENDING for *manual*
    retry. No automatic background retries unless cfg.auto_retry is on.
The user never loses a dictation, and the app never floods the API on its own.
"""
from __future__ import annotations

import logging
import os
import tempfile
import time
from typing import Callable, Optional

from . import audio as audiolib
from .errors import AuthError, ProviderError
from .state import State, StateMachine
from .storage import STATUS_DONE, STATUS_PENDING, Storage

log = logging.getLogger("spokengo.pipeline")

MAX_RETRY_ATTEMPTS = 3


class VoicePipeline:
    def __init__(self, *, state: StateMachine, storage: Storage, injector,
                 provider, config, notify: Optional[Callable[[str, str], None]] = None,
                 clock: Callable[[], float] = time.time):
        self.state = state
        self.storage = storage
        self.injector = injector
        self.provider = provider
        self.config = config
        self.notify = notify or (lambda level, msg: None)
        self._clock = clock

    def _wav_from_frames(self, frames: bytes) -> str:
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        return audiolib.write_wav(frames, self.config.sample_rate, path)

    def process(self, frames: bytes, target=None) -> Optional[int]:
        try:
            if not frames or audiolib.is_silent(frames, self.config.silence_rms_threshold):
                self.notify("info", "Пустая запись — пропускаю")
                self.state.reset()
                return None

            duration = audiolib.frames_to_seconds(frames, self.config.sample_rate)
            wav_path = self._wav_from_frames(frames)
            stored_audio = None
            if self.config.store_audio:
                with open(wav_path, "rb") as f:
                    stored_audio = self.storage.save_audio(f.read())

            try:
                tr = self.provider.transcribe(
                    wav_path, model=self.config.model, language=self.config.language)
            except ProviderError as exc:
                return self._handle_provider_error(exc, duration, stored_audio)
            finally:
                try:
                    os.remove(wav_path)
                except OSError:
                    pass

            text = (tr.text or "").strip()
            if not text:
                self.notify("info", "Пустой результат распознавания")
                self.state.reset()
                return None

            rid = self.storage.add(text=text, provider=self.provider.name,
                                   model=self.config.model, duration=duration,
                                   audio_path=stored_audio, status=STATUS_DONE)
            self.state.begin_injecting()
            ok = self.injector.inject(target, text)
            if not ok:
                self.notify("warn", "Не удалось вставить — текст в буфере обмена.")
            self.state.reset()
            return rid
        except Exception as exc:
            log.exception("pipeline.process failed")
            self.notify("error", f"Сбой: {exc}")
            try:
                self.state.fail()
            finally:
                self.state.reset()
            return None

    def _handle_provider_error(self, exc: ProviderError, duration, stored_audio):
        log.error("provider error (transient=%s): %s",
                  getattr(exc, "transient", False), exc)
        if getattr(exc, "transient", False):
            rid = self.storage.add(text="", provider=self.provider.name,
                                   model=self.config.model, duration=duration,
                                   audio_path=stored_audio, status=STATUS_PENDING)
            self.notify("warn", f"{exc} Записал в очередь — повторите вручную.")
            self.state.fail(); self.state.reset()
            return rid
        # permanent: do NOT queue, do NOT retry
        self.storage.add(text="", provider=self.provider.name,
                         model=self.config.model, duration=duration,
                         audio_path=stored_audio, status="failed")
        level = "error" if isinstance(exc, AuthError) else "error"
        self.notify(level, str(exc))
        self.state.fail(); self.state.reset()
        return None

    def retry_pending(self, max_attempts: int = MAX_RETRY_ATTEMPTS) -> int:
        """Manual/bounded retry of queued recordings. Returns count completed.
        Exhausted items are marked FAILED so they stop being retried."""
        done = 0
        for rec in self.storage.pending(max_attempts=max_attempts):
            if not rec.audio_path or not os.path.exists(rec.audio_path):
                self.storage.mark_failed(rec.id)
                continue
            n = self.storage.bump_attempt(rec.id)
            try:
                tr = self.provider.transcribe(
                    rec.audio_path, model=rec.model or self.config.model,
                    language=self.config.language)
            except ProviderError as exc:
                if not getattr(exc, "transient", False) or n >= max_attempts:
                    self.storage.mark_failed(rec.id)
                continue
            self.storage.mark_done(rec.id, (tr.text or "").strip())
            done += 1
        return done
