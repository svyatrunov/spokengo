"""Recording lifecycle as an explicit state machine.

Most bugs in "voice into any field" tools are concurrency races: a second
hotkey arriving during transcription, key auto-repeat firing twice, a paste
landing while audio is still being captured. We make those *illegal transitions*
rather than patching their symptoms. A debounce window absorbs key auto-repeat
and duplicate hooks.
"""
from __future__ import annotations

import threading
import time
from enum import Enum
from typing import Callable, Optional

from .errors import InvalidTransition


class State(str, Enum):
    IDLE = "idle"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    INJECTING = "injecting"
    ERROR = "error"


# Allowed transitions. Anything not listed raises InvalidTransition.
_ALLOWED = {
    State.IDLE: {State.RECORDING},
    State.RECORDING: {State.TRANSCRIBING, State.IDLE, State.ERROR},
    State.TRANSCRIBING: {State.INJECTING, State.IDLE, State.ERROR},
    State.INJECTING: {State.IDLE, State.ERROR},
    State.ERROR: {State.IDLE},
}


class StateMachine:
    """Thread-safe lifecycle guard.

    ``mode`` is "toggle" (press starts, press stops) or "push" (press starts,
    release stops). ``debounce_ms`` ignores hotkey edges that arrive too close
    together — that is key auto-repeat or a duplicated low-level hook.
    """

    def __init__(
        self,
        mode: str = "toggle",
        debounce_ms: int = 250,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if mode not in ("toggle", "push"):
            raise ValueError("mode must be 'toggle' or 'push'")
        self.mode = mode
        self.debounce_s = debounce_ms / 1000.0
        self._clock = clock
        self._state = State.IDLE
        self._last_edge = -1e9
        self._lock = threading.RLock()
        self.on_change: Optional[Callable[[State, State], None]] = None

    @property
    def state(self) -> State:
        with self._lock:
            return self._state

    def _set(self, new: State) -> None:
        old = self._state
        if new != old and new not in _ALLOWED.get(old, set()):
            raise InvalidTransition(f"{old.value} -> {new.value} not allowed")
        self._state = new
        if new != old and self.on_change:
            self.on_change(old, new)

    # --- hotkey edges -------------------------------------------------

    def on_hotkey_press(self) -> Optional[str]:
        """Return an action: 'start', 'stop', or None (debounced/ignored)."""
        with self._lock:
            now = self._clock()
            if now - self._last_edge < self.debounce_s:
                return None  # auto-repeat / duplicate hook
            self._last_edge = now
            if self._state is State.IDLE:
                self._set(State.RECORDING)
                return "start"
            if self.mode == "toggle" and self._state is State.RECORDING:
                self._set(State.TRANSCRIBING)
                return "stop"
            # Press during transcribing/injecting, or push-mode press while
            # already recording: ignore. Guards bug #11.
            return None

    def on_hotkey_release(self) -> Optional[str]:
        with self._lock:
            if self.mode == "push" and self._state is State.RECORDING:
                self._set(State.TRANSCRIBING)
                return "stop"
            return None

    # --- pipeline-driven transitions ----------------------------------

    def try_start(self) -> bool:
        """Start-only edge (for a dedicated start hotkey). Debounced. Returns
        True if we moved IDLE -> RECORDING, else False (already busy / repeat)."""
        with self._lock:
            now = self._clock()
            if now - self._last_edge < self.debounce_s:
                return False
            self._last_edge = now
            if self._state is State.IDLE:
                self._set(State.RECORDING)
                return True
            return False

    def ensure_transcribing(self) -> bool:
        """Idempotently move RECORDING -> TRANSCRIBING. Returns True if we are
        (now) transcribing, False if we were not recording (ignore the stop).
        Used by both the toggle-stop and the auto-stop paths so a stop can never
        fire twice or from the wrong state."""
        with self._lock:
            if self._state is State.RECORDING:
                self._set(State.TRANSCRIBING)
                return True
            return self._state is State.TRANSCRIBING

    def begin_injecting(self) -> None:
        with self._lock:
            self._set(State.INJECTING)

    def finish(self) -> None:
        with self._lock:
            self._set(State.IDLE)

    def fail(self) -> None:
        """Move to ERROR from anywhere (always reachable). Caller then reset()s."""
        with self._lock:
            old = self._state
            self._state = State.ERROR
            if old != State.ERROR and self.on_change:
                self.on_change(old, State.ERROR)

    def reset(self) -> None:
        with self._lock:
            self._set(State.IDLE)
