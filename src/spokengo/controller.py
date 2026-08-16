"""UI-agnostic controller between any front-end and the core pipeline. All
GUI-independent logic lives here so it is testable without a display, mic,
Windows, or network.

Stop path (the fix for the request-flood bug): both the toggle-stop and the
duration auto-stop funnel through ONE lock-guarded ``_finish_recording`` that
(a) transitions the state machine exactly once, (b) detaches the recorder so it
can't be stopped twice, and (c) spawns exactly one transcription. There is no
automatic background retry — queued items are retried only on explicit request.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable, List, Optional

from .config import Config, default_config_dir, load_config, save_config
from .secrets_store import get_key, set_key
from .overlay import NullOverlay
from .state import State, StateMachine
from .storage import Record, Storage

log = logging.getLogger("spokengo.controller")

# How often the watchdog checks whether a transcription worker is still alive.
WATCHDOG_POLL_S = 5.0


class _NullInjector:
    def capture_target(self):
        return None
    def inject(self, target, text) -> bool:
        return False


def _default_injector_factory(cfg: Config):
    try:
        from .inject.windows import make_injector
        return make_injector(cfg.inject_fallback_typing,
                             getattr(cfg, "restore_clipboard", False))
    except Exception:
        return _NullInjector()


def _default_recorder_factory(cfg: Config):
    from .audio import AudioRecorder
    # local Whisper has no server-side size limit — disable the auto-stop guard
    max_sec = 0 if cfg.provider == "local" else cfg.max_seconds
    return AudioRecorder(cfg.sample_rate, max_sec)


def _default_provider_factory(cfg: Config):
    from .transcribe import get_provider
    if cfg.provider == "local":
        return get_provider("local")(cfg.local_model)
    key = get_key(cfg.provider)
    if not key:
        raise RuntimeError(f"API-ключ для '{cfg.provider}' не задан")
    return get_provider(cfg.provider)(key)


class GuiController:
    def __init__(self, *, cfg: Optional[Config] = None, root_dir: Optional[Path] = None,
                 storage: Optional[Storage] = None,
                 injector_factory: Callable[[Config], object] = _default_injector_factory,
                 recorder_factory: Callable[[Config], object] = _default_recorder_factory,
                 provider_factory: Callable[[Config], object] = _default_provider_factory):
        self.cfg = cfg or load_config()
        self.root_dir = Path(root_dir or default_config_dir())
        self.storage = storage or Storage(self.root_dir)
        self.state = StateMachine(mode=self.cfg.mode, debounce_ms=self.cfg.debounce_ms)
        self._injector_factory = injector_factory
        self._recorder_factory = recorder_factory
        self._provider_factory = provider_factory
        self._injector = None
        self._recorder = None
        self._target = None
        self._stop_lock = threading.Lock()
        self._worker: Optional[threading.Thread] = None
        self._watchdog: Optional[threading.Timer] = None
        self.overlay = NullOverlay()  # set to a TkOverlay by the GUI
        self.on_recording_started = None  # GUI hook: register Enter/Esc
        self.on_recording_stopped = None  # GUI hook: unregister Enter/Esc
        self.on_status: Optional[Callable[[State, str], None]] = None
        self.on_cancelled: Optional[Callable[[int], None]] = None  # hook: (rid,)
        self._cancel_timers: dict = {}

    # --- helpers -------------------------------------------------------
    def has_key(self) -> bool:
        return bool(get_key(self.cfg.provider))

    def stored_key(self) -> str:
        """The raw stored key (for prefilling the masked field). '' if none."""
        return get_key(self.cfg.provider) or ""

    def key_hint(self) -> str:
        """Masked hint of the stored key, e.g. 'gsk_…7Af2', or '' if none."""
        k = get_key(self.cfg.provider) or ""
        if not k:
            return ""
        return (k[:4] + "…" + k[-4:]) if len(k) > 9 else "•••"

    def set_api_key(self, key: str) -> None:
        key = (key or "").strip()
        if not key:
            self._emit("Пустой ключ — ничего не изменено")
            return
        set_key(self.cfg.provider, key)
        self._emit(f"Ключ сохранён ({self.key_hint()})")

    def clear_api_key(self) -> None:
        from .secrets_store import delete_key
        delete_key(self.cfg.provider)
        self._emit("Ключ удалён")

    def _emit(self, message: str) -> None:
        log.info("[%s] %s", self.state.state.value, message)
        if self.on_status:
            self.on_status(self.state.state, message)

    def _injector_obj(self):
        if self._injector is None:
            self._injector = self._injector_factory(self.cfg)
        return self._injector

    # --- settings ------------------------------------------------------
    def save_settings(self, *, hotkey=None, mode=None, model=None, language=None,
                      provider=None, store_audio=None, api_key=None,
                      local_model=None, max_seconds=None, max_storage_mb=None) -> None:
        if hotkey is not None: self.cfg.hotkey = hotkey
        if mode is not None: self.cfg.mode = mode
        if model is not None: self.cfg.model = model
        if language is not None: self.cfg.language = (language or None)
        if provider is not None: self.cfg.provider = provider
        if store_audio is not None: self.cfg.store_audio = store_audio
        if local_model is not None: self.cfg.local_model = local_model
        if max_seconds is not None: self.cfg.max_seconds = max_seconds
        if max_storage_mb is not None: self.cfg.max_storage_mb = max_storage_mb
        self.cfg.validate()
        save_config(self.cfg, self.root_dir / "config.toml")
        if api_key:  # only overwrite the stored key when a new one is supplied
            set_key(self.cfg.provider, api_key)
        if mode is not None and self.state.mode != self.cfg.mode:
            self.state = StateMachine(mode=self.cfg.mode, debounce_ms=self.cfg.debounce_ms)
        self._emit("Настройки сохранены")

    def recent(self, n: int = 30) -> List[Record]:
        return self.storage.recent(n)

    def peek_target(self):
        """Current paste target (foreground app), for the live overlay. Safe to
        call repeatedly; returns None if unavailable."""
        try:
            return self._injector_obj().capture_target()
        except Exception:
            return None

    def last_text(self) -> str:
        """Most recent non-empty transcript (for the one-click copy)."""
        for r in self.storage.recent(20):
            if r.text:
                return r.text
        return ""

    def retry_one(self, rec_id) -> bool:
        """Retry a single recording by id (works for queued or failed items)."""
        rec = self.storage.get(rec_id)
        if not rec or not rec.audio_path:
            self._emit("Нет аудио для повтора этой записи")
            return False
        try:
            provider = self._provider_factory(self.cfg)
        except Exception as exc:
            self._emit(str(exc)); return False
        from .pipeline import VoicePipeline
        pipe = VoicePipeline(state=self.state, storage=self.storage,
                             injector=self._injector_obj(), provider=provider,
                             config=self.cfg, notify=lambda l, m: self._emit(m))
        self.storage.reset_pending(rec_id)          # allow re-trying a failed item
        ok = pipe.retry_record(self.storage.get(rec_id))
        self._emit("Повтор: готово" if ok else "Повтор не удался — проверьте сеть/VPN")
        return ok

    def clear_history(self) -> None:
        self.storage.clear_all()
        self._emit("История очищена")

    def retry_queue(self) -> int:
        """Manually retry queued (transient-failure) recordings. Bounded."""
        try:
            provider = self._provider_factory(self.cfg)
        except Exception as exc:
            self._emit(str(exc)); return 0
        from .pipeline import VoicePipeline
        pipe = VoicePipeline(state=self.state, storage=self.storage,
                             injector=self._injector_obj(), provider=provider,
                             config=self.cfg, notify=lambda l, m: self._emit(m))
        done = pipe.retry_pending()
        self._emit(f"Повтор очереди: распознано {done}")
        return done

    # --- recording -----------------------------------------------------
    def toggle(self) -> str:
        """Used by the window button: start if idle, else stop."""
        if self.state.state is State.IDLE:
            return "start" if self.start_recording() else "ignored"
        if self.state.state is State.RECORDING:
            self.stop_recording()
            return "stop"
        # TRANSCRIBING/INJECTING/ERROR with no live worker means we are wedged —
        # the button is the user's escape hatch, so recover instead of ignoring.
        w = self._worker
        if w is None or not w.is_alive():
            self.force_reset()
            return "reset"
        return "ignored"

    def start_recording(self) -> bool:
        """Start-only (the start hotkey / button). Returns True if started."""
        if not self.state.try_start():
            return False
        try:
            self._target = self._injector_obj().capture_target()
            rec = self._recorder_factory(self.cfg)
            rec.start(on_autostop=self._on_autostop)
            # Publish the recorder only once it is actually capturing. A stop
            # arriving before this point finds _recorder = None; _finish_recording
            # unwedges the state, and the check below closes the stream we just
            # opened instead of leaving the mic hot with nobody to stop it.
            with self._stop_lock:
                if self.state.state is not State.RECORDING:
                    stale = True
                else:
                    self._recorder, stale = rec, False
            if stale:
                try:
                    rec.stop()
                except Exception:
                    pass
                self.overlay.hide()
                self._emit_idle("Запись отменена до старта")
                return False
            self.overlay.show(self._target)
            if self.on_recording_started:
                self.on_recording_started()   # GUI registers Enter/Esc
            self._emit("Запись…")
            return True
        except Exception as exc:
            log.exception("start_recording failed")
            with self._stop_lock:
                self._recorder = None
            self.state.fail(); self.state.reset()
            self.overlay.hide()
            self._emit(f"Не удалось начать запись: {exc}")
            return False

    def stop_recording(self) -> None:
        self._finish_recording()

    def cancel_recording(self) -> None:
        """Cancel current recording — saves to history; audio purged after 60 s."""
        with self._stop_lock:
            if self.state.state is not State.RECORDING:
                return
            rec, self._recorder = self._recorder, None
            self.state.reset()
        frames = None
        if rec is not None:
            try:
                frames = rec.stop()
            except Exception:
                pass
        self.overlay.hide()
        if self.on_recording_stopped:
            self.on_recording_stopped()
        self._save_cancelled(frames)

    def _save_cancelled(self, frames: Optional[bytes]) -> None:
        import os, tempfile, threading
        from . import audio as audiolib
        from .storage import STATUS_CANCELLED
        try:
            wav_path = None
            if self.cfg.store_audio and frames:
                fd, tmp = tempfile.mkstemp(suffix=".wav")
                os.close(fd)
                try:
                    audiolib.write_wav(frames, self.cfg.sample_rate, tmp)
                    with open(tmp, "rb") as f:
                        wav_path = self.storage.save_audio(f.read())
                finally:
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
            dur = len(frames) / (self.cfg.sample_rate * 2) if frames else 0.0
            rid = self.storage.add(text="", provider="", model="",
                                   duration=dur, audio_path=wav_path,
                                   status=STATUS_CANCELLED)
            if wav_path:
                def _cleanup():
                    self.storage.clear_audio(rid)
                    self._cancel_timers.pop(rid, None)
                t = threading.Timer(60, _cleanup)
                t.daemon = True
                self._cancel_timers[rid] = t
                t.start()
            if self.on_cancelled:
                self.on_cancelled(rid)
            self._emit("Запись отменена — аудио ещё 60 с" if wav_path else "Отменено")
        except Exception:
            log.exception("_save_cancelled failed")
            self._emit("Отменено")

    def keep_cancelled(self, rid: int) -> None:
        """Cancel the 60-s audio purge; convert record to pending for retry."""
        t = self._cancel_timers.pop(rid, None)
        if t:
            t.cancel()
        self.storage.reset_pending(rid)
        self._emit("Аудио сохранено — нажмите «Повторить» для распознавания")

    def _on_autostop(self) -> None:
        """Called from the audio thread when the duration limit is reached."""
        self._emit("Достигнут лимит длительности — останавливаю")
        self._finish_recording()

    def _arm_watchdog(self, worker: threading.Thread) -> None:
        """Last-resort unwedge. If the transcription worker dies without
        restoring IDLE (an unexpected crash, a killed thread), the app would sit
        in TRANSCRIBING and ignore every hotkey. Poll the worker and force the
        state back so the user is never stuck without a restart."""
        def _check():
            if worker.is_alive():
                t = threading.Timer(WATCHDOG_POLL_S, _check)
                t.daemon = True
                self._watchdog = t
                t.start()
                return
            if self.state.state is not State.IDLE:
                log.warning("watchdog: worker died in %s — forcing IDLE",
                            self.state.state.value)
                try:
                    self.state.fail()
                finally:
                    self.state.reset()
                self._emit_idle("Распознавание прервалось — запись в истории")
        t = threading.Timer(WATCHDOG_POLL_S, _check)
        t.daemon = True
        self._watchdog = t
        t.start()

    def force_reset(self) -> None:
        """Manual escape hatch: drop any half-finished state back to IDLE."""
        with self._stop_lock:
            rec, self._recorder = self._recorder, None
        if rec is not None:
            try:
                rec.stop()
            except Exception:
                pass
        if self.on_recording_stopped:
            self.on_recording_stopped()
        if self.state.state is not State.IDLE:
            try:
                self.state.fail()
            finally:
                self.state.reset()
        self._emit_idle()

    def _finish_recording(self) -> None:
        with self._stop_lock:
            # Whether *we* are the edge that ends the recording. ensure_transcribing
            # also returns True for an already-transcribing state, so it alone
            # cannot tell a first stop from a duplicate one.
            was_recording = self.state.state is State.RECORDING
            if not self.state.ensure_transcribing():
                return  # not recording -> ignore (idempotent, kills the flood)
            rec, self._recorder = self._recorder, None
            if rec is None and not was_recording:
                return  # duplicate stop; the first one owns this transcription
            orphaned = rec is None
            if orphaned:
                self.state.reset()      # hand TRANSCRIBING back before we bail
        if orphaned:
            # We ended the recording but there is no recorder to stop — the start
            # path had not attached one yet. Returning without the reset above
            # would park the state machine in TRANSCRIBING forever: every later
            # Enter/hotkey/button press is then silently ignored (neither IDLE
            # nor RECORDING) and the app is dead until restart.
            if self.on_recording_stopped:
                self.on_recording_stopped()
            self._emit_idle("Запись не началась — попробуйте ещё раз")
            return
        if self.on_recording_stopped:
            self.on_recording_stopped()       # GUI unregisters Enter/Esc
        self.overlay.set_state(State.TRANSCRIBING, "Распознаю…")
        self._emit("Распознаю…")
        try:
            frames = rec.stop()
        except Exception as exc:
            self.state.fail(); self.state.reset()
            self.overlay.hide()
            self._emit(f"Ошибка записи: {exc}")
            return
        worker = threading.Thread(target=self._process, args=(frames,), daemon=True)
        self._worker = worker
        worker.start()
        self._arm_watchdog(worker)

    def _process(self, frames: bytes) -> None:
        from .pipeline import VoicePipeline
        try:
            provider = self._provider_factory(self.cfg)
        except Exception as exc:
            log.exception("provider build failed")
            self.state.fail(); self.state.reset()
            self._emit(f"{exc}. Откройте «Настройки» и вставьте ключ API.")
            self._emit_idle()
            return
        pipe = VoicePipeline(state=self.state, storage=self.storage,
                             injector=self._injector_obj(), provider=provider,
                             config=self.cfg, notify=lambda lvl, msg: self._emit(msg))
        idle_msg = "Готов. Нажмите Запись или Ctrl+Space"
        try:
            rid, inject_ok = pipe.process(frames, self._target)
            if rid and not inject_ok:
                rec = self.storage.get(rid)
                if rec and rec.text:
                    idle_msg = f"Сохранено в историю: {rec.text[:80]}"
            if self.cfg.max_storage_mb > 0:
                self.storage.evict_oldest(self.cfg.max_storage_mb)
        except Exception as exc:
            log.exception("_process: unexpected error")
            self._emit(f"Неожиданная ошибка: {exc}")
        finally:
            if self.state.state is not State.IDLE:
                self.state.reset()
            self._emit_idle(idle_msg)

    def _emit_idle(self, message: str = "Готов. Нажмите Запись или Ctrl+Space") -> None:
        self.overlay.hide()
        if self.on_status:
            self.on_status(State.IDLE, message)
