"""Headless orchestrator (``spokengo run``): global hotkey -> the same
GuiController used by the window. Start = hotkey, stop = Enter, cancel = Esc
(Enter/Esc active only while recording). Single-instance guarded.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from .config import Config, load_config
from .controller import GuiController
from .state import State


class App:
    def __init__(self, cfg: Config | None = None, root: Path | None = None):
        self.cfg = cfg or load_config()
        self.ctrl = GuiController(cfg=self.cfg, root_dir=root)
        self.ctrl.on_status = self._log
        self.ctrl.on_recording_started = self._reg_rec_keys
        self.ctrl.on_recording_stopped = self._unreg_rec_keys
        self._hk = None
        self._rec_ids = []

    def _log(self, state: State, message: str) -> None:
        print(f"[{state.value}] {message}")

    def _reg_rec_keys(self):
        if not self._hk:
            return
        self._rec_ids = [
            self._hk.register(self.cfg.stop_key, self.ctrl.stop_recording),
            self._hk.register(self.cfg.cancel_key, self.ctrl.cancel_recording),
        ]

    def _unreg_rec_keys(self):
        for hk in self._rec_ids:
            try:
                self._hk.unregister(hk)
            except Exception:
                pass
        self._rec_ids = []

    def run(self):  # pragma: no cover - requires Windows + devices
        from .single_instance import acquire
        if not acquire():
            print("SpokenGo уже запущен.")
            return
        from .hotkeys import make_hotkey_manager
        self._hk = make_hotkey_manager()
        self._hk.start(on_error=lambda combo: print(
            f"Сочетание «{combo}» занято — смените хоткей."))
        self._hk.register(self.cfg.hotkey, self.ctrl.start_recording)
        if self.cfg.auto_retry:
            threading.Thread(target=self._queue_loop, daemon=True).start()
        print(f"SpokenGo запущен. Старт: {self.cfg.hotkey}, стоп: Enter. Ctrl+C — выход.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self._hk.stop()

    def _queue_loop(self):  # pragma: no cover
        while True:
            time.sleep(300)
            try:
                self.ctrl.retry_queue()
            except Exception:
                pass
