"""Reusable settings models."""

from __future__ import annotations

import importlib
import sys

__all__ = [
    "DatabaseSettings",
    "GrpcSettings",
    "InternalSettings",
    "MissingRequirement",
    "Need",
    "RedisCoordinationSettings",
    "check_configured",
    "needs",
    "prefixes_for",
    "require_configured",
]

_EXPORT_MODULES = {
    "DatabaseSettings": ".config",
    "GrpcSettings": ".config",
    "InternalSettings": ".config",
    "RedisCoordinationSettings": ".config",
    # `needs` depends on nothing optional — a service can declare what it needs
    # without the env extra installed, and without a control-plane to answer.
    "MissingRequirement": ".needs",
    "Need": ".needs",
    "check_configured": ".needs",
    "needs": ".needs",
    "prefixes_for": ".needs",
    "require_configured": ".needs",
}


def __getattr__(name: str):
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(name)

    try:
        module = importlib.import_module(module_name, __name__)
    except ModuleNotFoundError as exc:  # pragma: no cover - runtime guard
        if exc.name == "msgspec_conf":
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
