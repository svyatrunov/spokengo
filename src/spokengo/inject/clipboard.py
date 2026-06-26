"""Clipboard paste cycle as pure logic over a ClipboardBackend interface.

This is where the nastiest races live, so it is fully testable without Windows:
  * snapshot the user's clipboard,
  * set our text and verify it actually landed before pasting,
  * trigger paste,
  * wait, then restore the original clipboard only AFTER the paste was consumed.
Retries cover "clipboard busy" (another process holds the lock).
"""
from __future__ import annotations

import time
from typing import Callable, Optional

from .base import ClipboardBackend


class ClipboardCycle:
    def __init__(self, backend: ClipboardBackend, *, retries: int = 5,
                 retry_delay: float = 0.02, restore_delay: float = 0.4,
                 sleep: Callable[[float], None] = time.sleep):
        self.backend = backend
        self.retries = retries
        self.retry_delay = retry_delay
        # How long to wait AFTER sending paste before restoring the user's old
        # clipboard. If we restore too soon, the target app (especially after a
        # cross-window focus switch) reads the OLD clipboard and pastes that
        # instead of the transcript. This delay closes that race.
        self.restore_delay = restore_delay
        self._sleep = sleep

    def _with_retry(self, fn):
        last = None
        for _ in range(self.retries):
            try:
                return fn()
            except Exception as exc:  # clipboard busy / locked
                last = exc
                self._sleep(self.retry_delay)
        raise last if last else RuntimeError("clipboard operation failed")

    def paste(self, text: str, do_paste: Callable[[], None], restore: bool = True) -> bool:
        """Insert ``text`` via the clipboard, calling ``do_paste`` (e.g. send
        Ctrl+V) at the right moment. Empty text is a no-op that never touches
        the clipboard (bug #6 guard). Returns True on success."""
        if not text:
            return False

        original = self._with_retry(self.backend.get_text)

        def _set_and_verify():
            self.backend.set_text(text)
            if self.backend.get_text() != text:
                raise RuntimeError("clipboard not yet updated")
            return True

        self._with_retry(_set_and_verify)   # ensures our text is really there
        do_paste()                           # paste only after verification
        if restore:
            # Give the target time to actually consume the paste before we put
            # the user's old clipboard back (otherwise it pastes the old text).
            self._sleep(self.restore_delay)
            self._with_retry(lambda: self.backend.set_text(original or ""))
        # When restore is False we deliberately LEAVE the transcript on the
        # clipboard, so the user can Ctrl+V it again anywhere — nothing is lost.
        return True
