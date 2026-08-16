"""Regression: a stop that arrives before the recorder is attached must not
park the state machine in TRANSCRIBING. That wedge made every later Enter /
hotkey / button press a silent no-op — the app looked like it was still
recording and nothing was ever saved, until a restart."""
from __future__ import annotations

from spokengo.config import Config
from spokengo.controller import GuiController
from spokengo.state import State
from spokengo.storage import Storage


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


def _ctrl(tmp_path, **kw):
    cfg = Config(store_audio=False)
    return GuiController(cfg=cfg, root_dir=tmp_path,
                         storage=Storage(tmp_path),
                         injector_factory=lambda c: _Injector(),
                         provider_factory=lambda c: None, **kw)


def test_stop_without_recorder_returns_to_idle(tmp_path):
    c = _ctrl(tmp_path, recorder_factory=lambda cfg: _Rec())
    assert c.state.try_start() is True          # RECORDING, _recorder still None
    c.stop_recording()                           # the racing stop
    assert c.state.state is State.IDLE, "stop before attach must not wedge"


def test_app_still_usable_after_the_race(tmp_path):
    c = _ctrl(tmp_path, recorder_factory=lambda cfg: _Rec())
    c.state.try_start()
    c.stop_recording()
    c.state.debounce_s = 0                       # ignore the auto-repeat guard
    assert c.start_recording() is True, "must be able to record again"
    assert c.state.state is State.RECORDING


def test_start_closes_stream_when_stop_won_the_race(tmp_path):
    """The recorder opened during the gap must be stopped, not left hot."""
    rec = _Rec()
    c = _ctrl(tmp_path, recorder_factory=lambda cfg: rec)

    real_start = rec.start

    def _start(on_autostop=None):
        real_start(on_autostop)
        c.state.reset()                          # a stop lands mid-start

    rec.start = _start
    assert c.start_recording() is False
    assert rec.stopped, "orphaned stream must be closed"
    assert c.state.state is State.IDLE


def test_button_recovers_a_wedged_state(tmp_path):
    c = _ctrl(tmp_path, recorder_factory=lambda cfg: _Rec())
    c.state.try_start()
    c.state.ensure_transcribing()                # simulate the old wedge
    assert c.toggle() == "reset"
    assert c.state.state is State.IDLE
