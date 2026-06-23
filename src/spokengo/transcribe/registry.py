"""Provider registry. Adding a model = one class + one decorator. The core
never changes. This is what makes "bring keys for popular models" extensible.
"""
from __future__ import annotations

from typing import Dict, List, Type

from ..errors import ConfigError

_REGISTRY: Dict[str, Type] = {}


def register_provider(name: str):
    def deco(cls):
        cls.name = name
        _REGISTRY[name] = cls
        return cls
    return deco


def get_provider(name: str):
    if name not in _REGISTRY:
        raise ConfigError(
            f"unknown provider {name!r}; available: {sorted(_REGISTRY)}")
    return _REGISTRY[name]


def available_providers() -> List[str]:
    return sorted(_REGISTRY)
