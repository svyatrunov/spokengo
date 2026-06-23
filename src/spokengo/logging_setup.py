"""File logging so production errors (especially the exact Groq response) are
visible after the fact. Logs go to %APPDATA%/SpokenGo/spokengo.log (rotating),
and also to the console. View with: ``spokengo logs``.
"""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path
from typing import Optional

from .config import default_config_dir

_configured = False


def log_path() -> Path:
    return default_config_dir() / "spokengo.log"


def setup_logging(level: int = logging.INFO, path: Optional[Path] = None,
                  force: bool = False, console: bool = True) -> Path:
    global _configured
    p = Path(path) if path else log_path()
    if _configured and not force:
        return p
    p.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("spokengo")
    logger.setLevel(level)
    for h in list(logger.handlers):
        logger.removeHandler(h)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    fh = logging.handlers.RotatingFileHandler(
        str(p), maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    if console:
        ch = logging.StreamHandler()
        ch.setFormatter(fmt)
        logger.addHandler(ch)
    logger.propagate = False
    _configured = True
    return p


def tail(path: Optional[Path] = None, lines: int = 100) -> str:
    p = Path(path) if path else log_path()
    if not p.exists():
        return ""
    data = p.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(data[-lines:])
