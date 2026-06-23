"""Settings (TOML) with safe defaults. Secrets live in secrets_store, never here.

Splitting settings from secrets is deliberate: an open-source tool must never
ship or commit an API key. config.toml is shareable; the key sits in the OS
credential store.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Dict

try:  # py311+ stdlib, else the tomli backport
    import tomllib as _toml_read
except ModuleNotFoundError:  # pragma: no cover
    import tomli as _toml_read  # type: ignore

from .errors import ConfigError


def default_config_dir() -> Path:
    base = os.environ.get("APPDATA") or os.path.expanduser("~/.config")
    return Path(base) / "SpokenGo"


@dataclass
class Config:
    hotkey: str = "ctrl+space"      # start recording
    stop_key: str = "enter"          # stop recording (active only while recording)
    cancel_key: str = "escape"       # discard recording
    mode: str = "toggle"              # toggle | push
    provider: str = "groq"
    model: str = "whisper-large-v3-turbo"
    language: str | None = None       # None = auto-detect
    sample_rate: int = 16000
    max_seconds: int = 60             # auto-stop guard (bug #18)
    store_audio: bool = True          # privacy opt-out (bug #26)
    audio_retention_days: int = 30
    silence_rms_threshold: float = 120.0  # below this = treat as silence
    debounce_ms: int = 250
    inject_fallback_typing: bool = True   # type char-by-char if paste fails
    auto_retry: bool = False              # background retry of the offline queue (off by default)

    @classmethod
    def _known(cls) -> set[str]:
        return {f.name for f in fields(cls)}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Config":
        known = cls._known()
        clean = {k: v for k, v in data.items() if k in known}
        cfg = cls(**clean)
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if self.mode not in ("toggle", "push"):
            raise ConfigError(f"mode must be toggle|push, got {self.mode!r}")
        if self.sample_rate <= 0:
            raise ConfigError("sample_rate must be positive")
        if self.max_seconds <= 0:
            raise ConfigError("max_seconds must be positive")

    def to_toml(self) -> str:
        lines = ["# SpokenGo config. The API key is NOT here (it is in the OS",
                 "# credential store). Edit values below.\n"]
        for k, v in asdict(self).items():
            if v is None:
                lines.append(f'# {k} = ""   # auto')
            elif isinstance(v, bool):
                lines.append(f"{k} = {str(v).lower()}")
            elif isinstance(v, str):
                lines.append(f'{k} = "{v}"')
            else:
                lines.append(f"{k} = {v}")
        return "\n".join(lines) + "\n"


def load_config(path: Path | None = None) -> Config:
    path = path or (default_config_dir() / "config.toml")
    if not path.exists():
        return Config()
    try:
        data = _toml_read.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # malformed TOML -> clear error, not a crash
        raise ConfigError(f"cannot parse {path}: {exc}") from exc
    return Config.from_dict(data)


def save_config(cfg: Config, path: Path | None = None) -> Path:
    path = path or (default_config_dir() / "config.toml")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cfg.to_toml(), encoding="utf-8")
    return path
