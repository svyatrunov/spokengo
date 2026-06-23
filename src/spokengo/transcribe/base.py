from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable


@dataclass
class Transcript:
    text: str
    language: Optional[str] = None
    duration: Optional[float] = None
    raw: Optional[dict] = None


@runtime_checkable
class TranscriptionProvider(Protocol):
    name: str

    def transcribe(self, audio_path: str, *, model: str,
                   language: Optional[str] = None) -> Transcript:
        ...
