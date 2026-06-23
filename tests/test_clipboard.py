import pytest

from spokengo.inject.clipboard import ClipboardCycle


class FakeClipboard:
    def __init__(self, busy_times=0):
        self.value = None
        self.busy_times = busy_times
        self.calls = 0
    def get_text(self):
        return self.value
    def set_text(self, text):
        if self.busy_times > 0:
            self.busy_times -= 1
            raise RuntimeError("clipboard busy")
        self.value = text


def test_paste_inserts_and_restores_original():
    cb = FakeClipboard(); cb.value = "ORIGINAL"
    seen = {}
    cyc = ClipboardCycle(cb, sleep=lambda s: None)
    def do_paste():
        seen["clip_at_paste"] = cb.value     # our text must be present here
    assert cyc.paste("распознанный текст", do_paste) is True
    assert seen["clip_at_paste"] == "распознанный текст"
    assert cb.value == "ORIGINAL"            # restored afterwards


def test_empty_text_never_touches_clipboard():
    cb = FakeClipboard(); cb.value = "KEEP"
    cyc = ClipboardCycle(cb, sleep=lambda s: None)
    assert cyc.paste("", lambda: None) is False
    assert cb.value == "KEEP"


def test_retries_when_clipboard_busy():
    cb = FakeClipboard(busy_times=2); cb.value = "ORIG"
    cyc = ClipboardCycle(cb, retries=5, sleep=lambda s: None)
    assert cyc.paste("hi", lambda: None) is True
    assert cb.value == "ORIG"


def test_gives_up_after_retries():
    cb = FakeClipboard(busy_times=99); cb.value = "ORIG"
    cyc = ClipboardCycle(cb, retries=3, sleep=lambda s: None)
    with pytest.raises(RuntimeError):
        cyc.paste("hi", lambda: None)


def test_restore_waits_for_consume_delay():
    """The old clipboard must be restored only AFTER restore_delay, so the
    target app pastes our text, not the previous clipboard (cross-window race)."""
    cb = FakeClipboard(); cb.value = "OLD"
    events = []
    def rec_sleep(d): events.append(("sleep", d))
    cyc = ClipboardCycle(cb, restore_delay=0.5, sleep=rec_sleep)
    def do_paste():
        events.append(("paste", cb.value))   # our text must be on clipboard now
    assert cyc.paste("TRANSCRIPT", do_paste) is True
    assert ("paste", "TRANSCRIPT") in events
    # a 0.5s restore wait happened, and it came after the paste
    assert ("sleep", 0.5) in events
    assert events.index(("paste", "TRANSCRIPT")) < events.index(("sleep", 0.5))
    assert cb.value == "OLD"                  # restored at the end
