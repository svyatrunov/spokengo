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


class _NullInjector:
    def capture_target(self):
        return None
    def inject(self, target, text) -> bool:
        return False


def _default_injector_factory(cfg: Config):
    try:
        from .inject.windows import make_injector
        return make_injector(cfg.inject_fallback_typing)
    except Exception:
        return _NullInjector()


def _default_recorder_factory(cfg: Config):
    from .audio import AudioRecorder
    return AudioRecorder(cfg.sample_rate, cfg.max_seconds)


def _default_provider_factory(cfg: Config):
    key = get_key(cfg.provider)
    if not key:
        raise RuntimeError(f"API-ключ для '{cfg.provider}' не задан")
    from .transcribe import get_provider
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
        self.overlay = NullOverlay()  # set to a TkOverlay by the GUI
        self.on_recording_started = None  # GUI hook: register Enter/Esc
        self.on_recording_stopped = None  # GUI hook: unregister Enter/Esc
        self.on_status: Optional[Callable[[State, str], None]] = None

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
                      provider=None, store_audio=None, api_key=None) -> None:
        if hotkey is not None: self.cfg.hotkey = hotkey
        if mode is not None: self.cfg.mode = mode
        if model is not None: self.cfg.model = model
        if language is not None: self.cfg.language = (language or None)
        if provider is not None: self.cfg.provider = provider
        if store_audio is not None: self.cfg.store_audio = store_audio
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
        return "ignored"

    def start_recording(self) -> bool:
        """Start-only (the start hotkey / button). Returns True if started."""
        if not self.state.try_start():
            return False
        try:
            self._target = self._injector_obj().capture_target()
            self._recorder = self._recorder_factory(self.cfg)
            self._recorder.start(on_autostop=self._on_autostop)
            self.overlay.show(self._target)
            if self.on_recording_started:
                self.on_recording_started()   # GUI registers Enter/Esc
            self._emit("Запись… Enter — стоп, Esc — отмена")
            return True
        except Exception as exc:
            log.exception("start_recording failed")
            self.state.fail(); self.state.reset()
            self.overlay.hide()
            self._emit(f"Не удалось начать запись: {exc}")
            return False

    def stop_recording(self) -> None:
        self._finish_recording()

    def cancel_recording(self) -> None:
        """Discard the current recording without transcribing (Esc)."""
        with self._stop_lock:
            if self.state.state is not State.RECORDING:
                return
            rec, self._recorder = self._recorder, None
            self.state.reset()
        if rec is not None:
            try:
                rec.stop()
            except Exception:
                pass
        self.overlay.hide()
        if self.on_recording_stopped:
            self.on_recording_stopped()
        self._emit("Отменено")

    def _on_autostop(self) -> None:
        """Called from the audio thread when the duration limit is reached."""
        self._emit("Достигнут лимит длительности — останавливаю")
        self._finish_recording()

    def _finish_recording(self) -> None:
        with self._stop_lock:
            if not self.state.ensure_transcribing():
                return  # not recording -> ignore (idempotent, kills the flood)
            rec, self._recorder = self._recorder, None
            if rec is None:
                return
        try:
            self._target = self._injector_obj().capture_target()  # target = NOW
        except Exception:
            pass
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
        threading.Thread(target=self._process, args=(frames,), daemon=True).start()

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
        rid = pipe.process(frames, self._target)
        if rid:
            rec = self.storage.get(rid)
            if rec and rec.text:
                self._emit(f"Готово: {rec.text[:60]}")
        if self.state.state is not State.IDLE:
            self.state.reset()
        self._emit_idle()

    def _emit_idle(self) -> None:
        self.overlay.hide()
        if self.on_status:
            self.on_status(State.IDLE, "Готов. Нажмите Запись или Ctrl+Space")
