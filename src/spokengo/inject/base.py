from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable


@dataclass
class Target:
    """Where text should land. ``handle`` is an opaque OS window handle (HWND on
    Windows). ``is_app`` is False for our own window / shell / desktop.
    ``process_name`` is the target executable (e.g. "Telegram.exe") — shown in
    the recording overlay so the user sees where the text will be inserted."""
    handle: Optional[int]
    title: str = ""
    is_app: bool = True
    process_name: str = ""


@runtime_checkable
class ClipboardBackend(Protocol):
    def get_text(self) -> Optional[str]: ...
    def set_text(self, text: str) -> None: ...


@runtime_checkable
class TextInjector(Protocol):
    def capture_target(self) -> Target: ...
    def inject(self, target: Target, text: str) -> bool: ...
