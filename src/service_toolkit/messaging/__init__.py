"""Messaging, NATS, and event helpers."""

from __future__ import annotations

import importlib
import sys

from .events import build_event, utc_now_iso

__all__ = [
    "BaseEventBus",
    "DEFAULT_NATS_URL",
    "GatewayCommandBus",
    "GatewayCommandBusConfig",
    "LeaderElectedListener",
    "NATSClient",
    "NATSSettings",
    "build_event",
    "utc_now_iso",
]

_OPTIONAL_EXPORT_MODULES = {
    "BaseEventBus": ".event_bus",
    "DEFAULT_NATS_URL": ".nats",
    "GatewayCommandBus": ".gateway_commands",
    "GatewayCommandBusConfig": ".gateway_commands",
    "LeaderElectedListener": ".leader_elected_listener",
    "NATSClient": ".nats",
    "NATSSettings": ".nats",
}


def __getattr__(name: str):
    module_name = _OPTIONAL_EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(name)

    try:
        module = importlib.import_module(module_name, __name__)
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
    return sorted(set(__all__))
