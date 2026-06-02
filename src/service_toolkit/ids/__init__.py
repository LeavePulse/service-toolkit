"""Identifier generation helpers."""

from __future__ import annotations

import importlib
import sys

__all__ = [
    "DEFAULT_EPOCH_MS",
    "Snowflake",
    "SnowflakeGenerator",
    "configure_default_generator",
    "generate_id",
    "reset_default_generator",
]

_EXPORT_MODULES = {
    "DEFAULT_EPOCH_MS": ".snowflake",
    "Snowflake": ".snowflake",
    "SnowflakeGenerator": ".snowflake",
    "configure_default_generator": ".snowflake",
    "generate_id": ".snowflake",
    "reset_default_generator": ".snowflake",
}
_SUBMODULES = {
    "snowflake": ".snowflake",
}


def __getattr__(name: str):
    module_name = _EXPORT_MODULES.get(name) or _SUBMODULES.get(name)
    if module_name is None:
        raise AttributeError(name)

    module = importlib.import_module(module_name, __name__)
    if name in _SUBMODULES:
        value = module
    else:
        value = getattr(module, name)
    setattr(sys.modules[__name__], name, value)
    return value


def __dir__() -> list[str]:  # pragma: no cover - reflection helper
    return sorted(set(__all__))
