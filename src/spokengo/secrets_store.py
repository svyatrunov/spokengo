"""API keys via the OS credential store (Windows Credential Manager) using
keyring. Falls back to an env var so CI and headless setups work. We never
write keys to config files or the repo.
"""
from __future__ import annotations

import os
from typing import Optional

_SERVICE = "SpokenGo"

try:
    import keyring  # type: ignore
    _HAVE_KEYRING = True
except Exception:  # pragma: no cover - optional dependency
    keyring = None  # type: ignore
    _HAVE_KEYRING = False


def _env_name(provider: str) -> str:
    return f"SPOKENGO_{provider.upper()}_API_KEY"


def set_key(provider: str, key: str) -> None:
    if _HAVE_KEYRING:
        try:
            keyring.set_password(_SERVICE, provider, key)
            return
        except Exception:
            # keyring present but no usable backend -> fall back to env var
            pass
    os.environ[_env_name(provider)] = key


def get_key(provider: str) -> Optional[str]:
    # Explicit env var always wins (handy for CI / containers).
    env = os.environ.get(_env_name(provider))
    if env:
        return env
    if _HAVE_KEYRING:
        try:
            return keyring.get_password(_SERVICE, provider)
        except Exception:
            return None
    return None


def delete_key(provider: str) -> None:
    if _HAVE_KEYRING:
        try:
            keyring.delete_password(_SERVICE, provider)
        except Exception:  # pragma: no cover
            pass
    os.environ.pop(_env_name(provider), None)
