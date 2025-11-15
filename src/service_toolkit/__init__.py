"""Shared infrastructure helpers for LeavePulse services."""

from __future__ import annotations

import importlib
import sys
from typing import TYPE_CHECKING

from .health import HealthController
from .logging import build_standard_logging_config
from .prometheus import build_prometheus_instrumentation
from .snowflake import (
    DEFAULT_EPOCH_MS,
    SnowflakeGenerator,
    configure_default_generator,
    generate_id,
    reset_default_generator,
)

if TYPE_CHECKING:  # pragma: no cover - import-time hinting
    from .nats import DEFAULT_NATS_URL, NATSClient, NATSSettings

__all__ = [
    "HealthController",
    "build_prometheus_instrumentation",
    "build_standard_logging_config",
    "DEFAULT_NATS_URL",
    "DEFAULT_EPOCH_MS",
    "NATSClient",
    "NATSSettings",
    "SnowflakeGenerator",
    "configure_default_generator",
    "generate_id",
    "reset_default_generator",
]

_OPTIONAL_EXPORTS = {"DEFAULT_NATS_URL", "NATSClient", "NATSSettings"}


def __getattr__(name: str):
    if name not in _OPTIONAL_EXPORTS:
        raise AttributeError(name)

    try:
        module = importlib.import_module(".nats", __name__)
    except ModuleNotFoundError as exc:  # pragma: no cover - runtime guard
        if exc.name == "nats":
            raise ModuleNotFoundError(
                "NATS helpers require the optional 'nats' extra. "
                "Install with 'pip install service-toolkit[nats]'."
            ) from exc
        raise

    value = getattr(module, name)
    setattr(sys.modules[__name__], name, value)
    return value


def __dir__() -> list[str]:  # pragma: no cover - reflection helper
    return sorted(set(__all__) | _OPTIONAL_EXPORTS)
