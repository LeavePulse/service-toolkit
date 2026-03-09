"""Shared infrastructure helpers for LeavePulse services."""

from __future__ import annotations

import importlib
import sys

from .observability.logging import (
    RequestContextLoggingMiddleware,
    bind_log_user_id,
    build_standard_logging_config,
)
from .observability.prometheus import build_prometheus_instrumentation
from .ids.snowflake import (
    DEFAULT_EPOCH_MS,
    SnowflakeGenerator,
    configure_default_generator,
    generate_id,
    reset_default_generator,
)
from .messaging.events import build_event, utc_now_iso
from .state.async_singleton import AsyncSingleton
from .state.cache import CacheMode, LookupCache, RedisFailureMode
from .web.health import HealthController
from .web.http import build_shared_async_client, close_shared_async_clients
from .web.rate_limit import (
    RateLimitFailureMode,
    enforce_request_rate_limit,
    rate_limited_request,
)
from .web.request_ip import resolve_client_ip

__all__ = [
    "AsyncSingleton",
    "CacheMode",
    "HealthController",
    "LookupCache",
    "RateLimitFailureMode",
    "RequestContextLoggingMiddleware",
    "RedisFailureMode",
    "bind_log_user_id",
    "build_prometheus_instrumentation",
    "build_shared_async_client",
    "build_standard_logging_config",
    "build_event",
    "close_shared_async_clients",
    "enforce_request_rate_limit",
    "rate_limited_request",
    "resolve_client_ip",
    "BaseEventBus",
    "DEFAULT_NATS_URL",
    "AuthSettings",
    "DatabaseSettings",
    "DBConfig",
    "DEFAULT_JWT_EXCLUDE",
    "InternalSettings",
    "JWTAuthMiddleware",
    "RedisCoordinationSettings",
    "ThrottledGaugeRefresh",
    "build_db_config",
    "create_service_app",
    "metric_label",
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
    "AuthSettings",
    "BaseEventBus",
    "DatabaseSettings",
    "DBConfig",
    "DEFAULT_JWT_EXCLUDE",
    "InternalSettings",
    "JWTAuthMiddleware",
    "RedisCoordinationSettings",
    "ThrottledGaugeRefresh",
    "build_db_config",
    "create_service_app",
    "metric_label",
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
    "AuthSettings": ".settings.config",
    "DatabaseSettings": ".settings.config",
    "DBConfig": ".db.litestar",
    "DEFAULT_JWT_EXCLUDE": ".web.app_factory",
    "InternalSettings": ".settings.config",
    "JWTAuthMiddleware": ".web.middleware",
    "RedisCoordinationSettings": ".settings.config",
    "build_db_config": ".db.litestar",
    "BaseEventBus": ".messaging.event_bus",
    "ThrottledGaugeRefresh": ".observability.metrics",
    "metric_label": ".observability.metrics",
    "create_service_app": ".web.app_factory",
    "DEFAULT_NATS_URL": ".messaging.nats",
    "NATSClient": ".messaging.nats",
    "NATSSettings": ".messaging.nats",
    "DEFAULT_REDIS_DB": ".state.redis",
    "DEFAULT_REDIS_HOST": ".state.redis",
    "DEFAULT_REDIS_PORT": ".state.redis",
    "Keyspace": ".state.redis",
    "LeaderElectedListener": ".messaging.leader_elected_listener",
    "LeaderLease": ".state.redis",
    "RedisClient": ".state.redis",
    "RedisLock": ".state.redis",
    "RedisReplicaStore": ".state.snapshot_store",
    "RedisSettings": ".state.redis",
    "setup_tracing": ".observability.tracing",
    "ttl_with_jitter": ".state.redis",
}


def __getattr__(name: str):
    if name not in _OPTIONAL_EXPORTS:
        raise AttributeError(name)

    try:
        module_name = _OPTIONAL_EXPORT_MODULES.get(name, ".nats")
        module = importlib.import_module(module_name, __name__)
    except ModuleNotFoundError as exc:  # pragma: no cover - runtime guard
        if exc.name == "env_settings":
            raise ModuleNotFoundError(
                "Config helpers require the optional 'env' extra. "
                "Install with 'pip install service-toolkit[env]'."
            ) from exc
        if exc.name == "advanced_alchemy":
            raise ModuleNotFoundError(
                "DB config helpers require the optional 'sqlalchemy' extra. "
                "Install with 'pip install service-toolkit[sqlalchemy]'."
            ) from exc
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
