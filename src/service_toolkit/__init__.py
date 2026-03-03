"""Shared infrastructure helpers for LeavePulse services."""

from __future__ import annotations

import importlib
import sys

from .health import HealthController
from .logging import (
    RequestContextLoggingMiddleware,
    bind_log_user_id,
    build_standard_logging_config,
)
from .cache import CacheMode, LookupCache, RedisFailureMode
from .prometheus import build_prometheus_instrumentation
from .snowflake import (
    DEFAULT_EPOCH_MS,
    SnowflakeGenerator,
    configure_default_generator,
    generate_id,
    reset_default_generator,
)
from .events import build_event, utc_now_iso
from .rate_limit import (
    RateLimitFailureMode,
    enforce_request_rate_limit,
    rate_limited_request,
)

__all__ = [
    "CacheMode",
    "HealthController",
    "LookupCache",
    "RateLimitFailureMode",
    "RequestContextLoggingMiddleware",
    "RedisFailureMode",
    "bind_log_user_id",
    "build_prometheus_instrumentation",
    "build_standard_logging_config",
    "build_event",
    "enforce_request_rate_limit",
    "rate_limited_request",
    "DEFAULT_NATS_URL",
    "DEFAULT_EPOCH_MS",
    "DEFAULT_REDIS_DB",
    "DEFAULT_REDIS_HOST",
    "DEFAULT_REDIS_PORT",
    "Keyspace",
    "LeaderElectedListener",
    "LeaderLease",
    "NATSClient",
    "NATSSettings",
    "RedisClient",
    "RedisLock",
    "RedisReplicaStore",
    "RedisSettings",
    "SnowflakeGenerator",
    "configure_default_generator",
    "generate_id",
    "reset_default_generator",
    "setup_tracing",
    "ttl_with_jitter",
    "utc_now_iso",
]

_OPTIONAL_EXPORTS = {
    "DEFAULT_NATS_URL",
    "NATSClient",
    "NATSSettings",
    "DEFAULT_REDIS_DB",
    "DEFAULT_REDIS_HOST",
    "DEFAULT_REDIS_PORT",
    "Keyspace",
    "LeaderElectedListener",
    "LeaderLease",
    "RedisClient",
    "RedisLock",
    "RedisReplicaStore",
    "RedisSettings",
    "setup_tracing",
    "ttl_with_jitter",
}

_OPTIONAL_EXPORT_MODULES = {
    "DEFAULT_NATS_URL": ".nats",
    "NATSClient": ".nats",
    "NATSSettings": ".nats",
    "DEFAULT_REDIS_DB": ".redis",
    "DEFAULT_REDIS_HOST": ".redis",
    "DEFAULT_REDIS_PORT": ".redis",
    "Keyspace": ".redis",
    "LeaderElectedListener": ".leader_elected_listener",
    "LeaderLease": ".redis",
    "RedisClient": ".redis",
    "RedisLock": ".redis",
    "RedisReplicaStore": ".snapshot_store",
    "RedisSettings": ".redis",
    "setup_tracing": ".tracing",
    "ttl_with_jitter": ".redis",
}


def __getattr__(name: str):
    if name not in _OPTIONAL_EXPORTS:
        raise AttributeError(name)

    try:
        module_name = _OPTIONAL_EXPORT_MODULES.get(name, ".nats")
        module = importlib.import_module(module_name, __name__)
    except ModuleNotFoundError as exc:  # pragma: no cover - runtime guard
        if exc.name == "nats":
            raise ModuleNotFoundError(
                "NATS helpers require the optional 'nats' extra. "
                "Install with 'pip install service-toolkit[nats]'."
            ) from exc
        if exc.name == "redis":
            raise ModuleNotFoundError(
                "Redis helpers require the optional 'redis' extra. "
                "Install with 'pip install service-toolkit[redis]'."
            ) from exc
        if exc.name and exc.name.startswith("opentelemetry"):
            raise ModuleNotFoundError(
                "Tracing helpers require the optional 'tracing' extra. "
                "Install with 'pip install service-toolkit[tracing]'."
            ) from exc
        raise

    value = getattr(module, name)
    setattr(sys.modules[__name__], name, value)
    return value


def __dir__() -> list[str]:  # pragma: no cover - reflection helper
    return sorted(set(__all__) | _OPTIONAL_EXPORTS)
