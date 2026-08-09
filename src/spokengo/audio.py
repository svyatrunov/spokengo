"""Audio capture. Device I/O (sounddevice) is isolated; the buffer math, WAV
writing, silence detection and the auto-stop guard are pure functions so they
test without a microphone.

The auto-stop callback is *single-fire*: once the duration limit is reached it
notifies exactly once, even though PortAudio keeps calling the callback. Without
this guard a long recording fires "stop" on every audio block — which previously
spawned a flood of transcription requests.
"""
from __future__ import annotations

import threading
import wave
from dataclasses import dataclass, field
from typing import List, Optional

from .errors import AudioError


def write_wav(frames: bytes, sample_rate: int, path: str, channels: int = 1,
              sampwidth: int = 2) -> str:
    with wave.open(path, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(sampwidth)
        w.setframerate(sample_rate)
        w.writeframes(frames)
    return path


def rms_int16(frames: bytes) -> float:
    n = len(frames) // 2
    if n == 0:
        return 0.0
    import array
    a = array.array("h")
    a.frombytes(frames[: n * 2])
    total = 0
    for s in a:
        total += s * s
    return (total / n) ** 0.5


def is_silent(frames: bytes, threshold: float) -> bool:
    return rms_int16(frames) < threshold


def frames_to_seconds(frames: bytes, sample_rate: int, channels: int = 1,
                      sampwidth: int = 2) -> float:
    return len(frames) / (sample_rate * channels * sampwidth)


def exceeds_limit(frames: bytes, sample_rate: int, max_seconds: int,
                  channels: int = 1, sampwidth: int = 2) -> bool:
    return frames_to_seconds(frames, sample_rate, channels, sampwidth) >= max_seconds


@dataclass
class Buffer:
    chunks: List[bytes] = field(default_factory=list)

    def add(self, chunk: bytes) -> None:
        self.chunks.append(chunk)

    def bytes(self) -> bytes:
        return b"".join(self.chunks)

    def clear(self) -> None:
        self.chunks.clear()


class AudioRecorder:
    """Thin wrapper over sounddevice. Imported lazily so the package imports on
    machines/CI without PortAudio."""

    def __init__(self, sample_rate: int = 16000, max_seconds: int = 60):
        self.sample_rate = sample_rate
        self.max_seconds = max_seconds
        self._buffer = Buffer()
        self._stream = None
        self._on_autostop = None
        self._autostop_fired = threading.Event()

    def _callback(self, indata, frames, time_info, status):  # pragma: no cover
        if self._autostop_fired.is_set():
            return  # already stopping — ignore further blocks (no flood)
        self._buffer.add(bytes(indata))
        if (self.max_seconds
                and exceeds_limit(self._buffer.bytes(), self.sample_rate, self.max_seconds)):
            # set the flag FIRST so concurrent callbacks bail, then fire once.
            # NEVER call stream.stop() from within the PortAudio callback — it is
            # forbidden by the PortAudio API and crashes. Dispatch to a new thread.
            if not self._autostop_fired.is_set():
                self._autostop_fired.set()
                cb = self._on_autostop
                if cb:
                    threading.Thread(target=cb, daemon=True).start()

    def start(self, on_autostop=None) -> None:  # pragma: no cover - needs device
        try:
            import sounddevice as sd
        except Exception as exc:
            raise AudioError(f"audio backend unavailable: {exc}") from exc
        self._buffer.clear()
        self._autostop_fired.clear()
        self._on_autostop = on_autostop
        try:
            # Shared-mode capture (PortAudio/WASAPI default): we coexist with
            # other apps recording at the same time (Telegram, Zoom, ...). We
            # open the device only for the duration of a dictation and close it
            # immediately after, so we never block other apps from the mic.
            self._stream = sd.RawInputStream(
                samplerate=self.sample_rate, channels=1, dtype="int16",
                callback=self._callback,
            )
            self._stream.start()
        except Exception as exc:
            raise AudioError(
                "Не удалось открыть микрофон — возможно, он занят другим "
                f"приложением в эксклюзивном режиме. ({exc})") from exc

    def stop(self) -> bytes:  # pragma: no cover - needs device
        self._autostop_fired.set()  # block any late callbacks
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None
        return self._buffer.bytes()
