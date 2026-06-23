import pytest

from spokengo.errors import InvalidTransition
from spokengo.state import State, StateMachine


class Clock:
    def __init__(self): self.t = 0.0
    def __call__(self): return self.t


def test_happy_path_toggle():
    c = Clock(); sm = StateMachine("toggle", clock=c)
    c.t = 1.0
    assert sm.on_hotkey_press() == "start"
    assert sm.state is State.RECORDING
    c.t = 5.0
    assert sm.on_hotkey_press() == "stop"
    assert sm.state is State.TRANSCRIBING
    sm.begin_injecting(); assert sm.state is State.INJECTING
    sm.finish(); assert sm.state is State.IDLE


def test_debounce_blocks_autorepeat():
    c = Clock(); sm = StateMachine("toggle", debounce_ms=250, clock=c)
    c.t = 1.0
    assert sm.on_hotkey_press() == "start"
    c.t = 1.1                       # 100ms < 250ms debounce
    assert sm.on_hotkey_press() is None
    assert sm.state is State.RECORDING


def test_rejects_concurrent_start_during_transcribing():
    c = Clock(); sm = StateMachine("toggle", clock=c)
    c.t = 1; sm.on_hotkey_press()
    c.t = 2; sm.on_hotkey_press()   # -> TRANSCRIBING
    c.t = 3
    assert sm.on_hotkey_press() is None  # ignored, no crash
    assert sm.state is State.TRANSCRIBING


def test_push_mode_stops_on_release():
    c = Clock(); sm = StateMachine("push", clock=c)
    c.t = 1
    assert sm.on_hotkey_press() == "start"
    assert sm.on_hotkey_release() == "stop"
    assert sm.state is State.TRANSCRIBING


def test_error_always_recovers_to_idle():
    sm = StateMachine("toggle")
    sm.fail()
    assert sm.state is State.ERROR
    sm.reset()
    assert sm.state is State.IDLE


def test_illegal_transition_raises():
    sm = StateMachine("toggle")
    with pytest.raises(InvalidTransition):
        sm.begin_injecting()  # IDLE -> INJECTING not allowed


def test_ensure_transcribing_idempotent():
    sm = StateMachine("toggle")
    sm._set(State.RECORDING)
    assert sm.ensure_transcribing() is True
    assert sm.state is State.TRANSCRIBING
    # second call: already transcribing -> still True, no error
    assert sm.ensure_transcribing() is True
    # from IDLE: nothing to stop -> False
    sm.reset()
    assert sm.ensure_transcribing() is False
