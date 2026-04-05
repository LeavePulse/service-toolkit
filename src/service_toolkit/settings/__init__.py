"""Reusable settings models."""

from __future__ import annotations

import importlib
import sys

__all__ = [
    "DatabaseSettings",
    "GrpcSettings",
    "InternalSettings",
    "RedisCoordinationSettings",
]

_EXPORT_MODULES = {
    "DatabaseSettings": ".config",
    "GrpcSettings": ".config",
    "InternalSettings": ".config",
    "RedisCoordinationSettings": ".config",
}


def __getattr__(name: str):
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(name)

    try:
        module = importlib.import_module(module_name, __name__)
    except ModuleNotFoundError as exc:  # pragma: no cover - runtime guard
        if exc.name == "env_settings":
            raise ModuleNotFoundError(
                "Settings helpers require the optional 'env' extra. "
                "Install with 'pip install service-toolkit[env]'."
            ) from exc
        raise

    value = getattr(module, name)
    setattr(sys.modules[__name__], name, value)
    return value


def __dir__() -> list[str]:  # pragma: no cover - reflection helper
    return sorted(set(__all__))
