"""Regression: the unwedge watchdog must never harm healthy work.

Shipped without these guards it aborted recordings that started after it was
armed, and — because it reset the state without detaching the recorder — left a
live PortAudio stream orphaned. The GC then finalised that stream from an
arbitrary thread while its callback could still run, crashing the process
(0xC0000005). Both failure modes are pinned here.
"""
from __future__ import annotations

import threading
import time

import spokengo.controller as ctrlmod
from spokengo.config import Config
from spokengo.controller import GuiController
from spokengo.state import State
from spokengo.storage import Storage

SETTLE = 0.25


class _Rec:
    def __init__(self):
        self.stopped = False

    def start(self, on_autostop=None):
        pass

    def stop(self):
        self.stopped = True
        return b""


class _Injector:
    def capture_target(self):
        return None

    def inject(self, target, text):
        return False


def _ctrl(tmp_path):
    return GuiController(cfg=Config(store_audio=False, debounce_ms=0),
                         root_dir=tmp_path, storage=Storage(tmp_path),
                         injector_factory=lambda c: _Injector(),
                         recorder_factory=lambda c: _Rec(),
                         provider_factory=lambda c: None)


def _dead_worker():
    t = threading.Thread(target=lambda: None)
    t.start()
    t.join()
    return t


def test_watchdog_leaves_a_newer_recording_alone(tmp_path, monkeypatch):
    """The exact production failure: armed during transcription, it fired ~5 s
    later into the *next* recording and killed it."""
    monkeypatch.setattr(ctrlmod, "WATCHDOG_POLL_S", 0.01)
    c = _ctrl(tmp_path)
    c._gen = 5
    c._arm_watchdog(_dead_worker(), 5)      # armed for cycle 5
    c.start_recording()                      # a new cycle bumps the generation
    assert c.state.state is State.RECORDING
    time.sleep(SETTLE)
    assert c.state.state is State.RECORDING, "watchdog aborted a newer recording"


def test_watchdog_never_acts_on_recording(tmp_path, monkeypatch):
    """RECORDING proves the app is alive — there is nothing to unwedge."""
    monkeypatch.setattr(ctrlmod, "WATCHDOG_POLL_S", 0.01)
    c = _ctrl(tmp_path)
    c.state.try_start()
    c._arm_watchdog(_dead_worker(), c._gen)  # same generation on purpose
    time.sleep(SETTLE)
    assert c.state.state is State.RECORDING


def test_watchdog_closes_the_stream_when_it_does_fire(tmp_path, monkeypatch):
    """A genuine wedge must be cleared *and* the capture stream closed, or the
    orphaned stream takes the process down later."""
    monkeypatch.setattr(ctrlmod, "WATCHDOG_POLL_S", 0.01)
    c = _ctrl(tmp_path)
    rec = _Rec()
    c.state.try_start()
    c.state.ensure_transcribing()            # a wedged TRANSCRIBING
    c._recorder = rec
    c._arm_watchdog(_dead_worker(), c._gen)
    time.sleep(SETTLE)
    assert c.state.state is State.IDLE, "a real wedge must still be cleared"
    assert rec.stopped, "orphaned capture stream must be closed, not leaked"
    assert c._recorder is None


def test_watchdog_reschedules_while_the_worker_lives(tmp_path, monkeypatch):
    monkeypatch.setattr(ctrlmod, "WATCHDOG_POLL_S", 0.01)
    c = _ctrl(tmp_path)
    c.state.try_start()
    c.state.ensure_transcribing()
    done = threading.Event()
    worker = threading.Thread(target=done.wait)
    worker.start()
    try:
        c._arm_watchdog(worker, c._gen)
        time.sleep(SETTLE)
        assert c.state.state is State.TRANSCRIBING, "must wait on a live worker"
    finally:
        done.set()
        worker.join()
