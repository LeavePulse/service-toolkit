"""Shared infrastructure helpers for LeavePulse services."""

from __future__ import annotations

import importlib
import sys

__all__ = [
    "AsyncSingleton",
    "BaseEventBus",
    "CacheMode",
    "DBConfig",
    "DEFAULT_EPOCH_MS",
    "DEFAULT_JWT_EXCLUDE",
    "DEFAULT_NATS_URL",
    "DEFAULT_REDIS_DB",
    "DEFAULT_REDIS_HOST",
    "DEFAULT_REDIS_PORT",
    "DatabaseSettings",
    "GrpcSettings",
    "GrpcClient",
    "GrpcClientMetricsInterceptor",
    "GrpcServerMetricsInterceptor",
    "InternalTokenCallCredentials",
    "InternalTokenClientInterceptor",
    "InternalTokenInterceptor",
    "HealthController",
    "InternalSettings",
    "JWTAuthIntegration",
    "Keyspace",
    "LeaderElectedListener",
    "LeaderLease",
    "LookupCache",
    "NATSClient",
    "NATSSettings",
    "ExpansionLoader",
    "ProjectionSpec",
    "RateLimitFailureMode",
    "RedisClient",
    "RedisCoordinationSettings",
    "RedisFailureMode",
    "RedisLock",
    "RedisReplicaStore",
    "RedisSettings",
    "RequestContextLoggingMiddleware",
    "ResponsePolicy",
    "SnowflakeGenerator",
    "ThrottledGaugeRefresh",
    "bind_log_user_id",
    "build_grpc_client",
    "build_grpc_lifecycle",
    "build_shared_channel",
    "close_shared_channels",
    "create_grpc_server",
    "start_grpc_server",
    "stop_grpc_server",
    "build_db_config",
    "build_event",
    "build_prometheus_instrumentation",
    "build_shared_async_client",
    "build_standard_logging_config",
    "close_shared_async_clients",
    "configure_default_generator",
    "create_service_app",
    "enforce_request_rate_limit",
    "generate_id",
    "metric_label",
    "rate_limited_request",
    "request_projection",
    "reset_default_generator",
    "resolve_client_ip",
    "setup_tracing",
    "ttl_with_jitter",
    "utc_now_iso",
]

_EXPORT_MODULES = {
    "AsyncSingleton": ".state.async_singleton",
    "BaseEventBus": ".messaging.event_bus",
    "CacheMode": ".state.cache",
    "DBConfig": ".db.litestar",
    "DEFAULT_EPOCH_MS": ".ids.snowflake",
    "DEFAULT_JWT_EXCLUDE": ".web.app_factory",
    "DEFAULT_NATS_URL": ".messaging.nats",
    "DEFAULT_REDIS_DB": ".state.redis",
    "DEFAULT_REDIS_HOST": ".state.redis",
    "DEFAULT_REDIS_PORT": ".state.redis",
    "DatabaseSettings": ".settings.config",
    "GrpcSettings": ".settings.config",
    "GrpcClient": ".grpc.client",
    "GrpcClientMetricsInterceptor": ".grpc.metrics",
    "GrpcServerMetricsInterceptor": ".grpc.metrics",
    "InternalTokenCallCredentials": ".grpc.interceptors",
    "InternalTokenClientInterceptor": ".grpc.interceptors",
    "InternalTokenInterceptor": ".grpc.interceptors",
    "HealthController": ".web.health",
    "InternalSettings": ".settings.config",
    "JWTAuthIntegration": ".web.jwt_integration",
    "Keyspace": ".state.redis",
    "LeaderElectedListener": ".messaging.leader_elected_listener",
    "LeaderLease": ".state.redis",
    "LookupCache": ".state.cache",
    "NATSClient": ".messaging.nats",
    "NATSSettings": ".messaging.nats",
    "ExpansionLoader": ".web.projection",
    "ProjectionSpec": ".web.projection",
    "RateLimitFailureMode": ".web.rate_limit",
    "RedisClient": ".state.redis",
    "RedisCoordinationSettings": ".settings.config",
    "RedisFailureMode": ".state.cache",
    "RedisLock": ".state.redis",
    "RedisReplicaStore": ".state.snapshot_store",
    "RedisSettings": ".state.redis",
    "RequestContextLoggingMiddleware": ".observability.logging",
    "ResponsePolicy": ".web.projection",
    "SnowflakeGenerator": ".ids.snowflake",
    "ThrottledGaugeRefresh": ".observability.metrics",
    "bind_log_user_id": ".observability.logging",
    "build_grpc_client": ".grpc.client",
    "build_grpc_lifecycle": ".grpc.server",
    "build_shared_channel": ".grpc.channels",
    "close_shared_channels": ".grpc.channels",
    "create_grpc_server": ".grpc.server",
    "start_grpc_server": ".grpc.server",
    "stop_grpc_server": ".grpc.server",
    "build_db_config": ".db.litestar",
    "build_event": ".messaging.events",
    "build_prometheus_instrumentation": ".observability.prometheus",
    "build_shared_async_client": ".web.http",
    "build_standard_logging_config": ".observability.logging",
    "close_shared_async_clients": ".web.http",
    "configure_default_generator": ".ids.snowflake",
    "create_service_app": ".web.app_factory",
    "enforce_request_rate_limit": ".web.rate_limit",
    "generate_id": ".ids.snowflake",
    "metric_label": ".observability.metrics",
    "rate_limited_request": ".web.rate_limit",
    "request_projection": ".web.projection",
    "reset_default_generator": ".ids.snowflake",
    "resolve_client_ip": ".web.request_ip",
    "setup_tracing": ".observability.tracing",
    "ttl_with_jitter": ".state.redis",
    "utc_now_iso": ".messaging.events",
}


def __getattr__(name: str):
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(name)

    try:
        module = importlib.import_module(module_name, __name__)
    except ModuleNotFoundError as exc:  # pragma: no cover - runtime guard
        raise _translate_optional_import_error(exc, module_name) from exc

    value = getattr(module, name)
    setattr(sys.modules[__name__], name, value)
    return value


def __dir__() -> list[str]:  # pragma: no cover - reflection helper
    return sorted(set(__all__))


def _translate_optional_import_error(
    exc: ModuleNotFoundError,
    module_name: str,
) -> ModuleNotFoundError:
    missing = exc.name or ""

    if missing == "env_settings":
        return ModuleNotFoundError(
            "Config helpers require the optional 'env' extra. "
            "Install with 'pip install service-toolkit[env]'."
        )
    if missing in {"advanced_alchemy", "sqlalchemy"}:
        return ModuleNotFoundError(
            "DB config helpers require the optional 'sqlalchemy' extra. "
            "Install with 'pip install service-toolkit[sqlalchemy]'."
        )
    if missing == "nats":
        return ModuleNotFoundError(
            "NATS helpers require the optional 'nats' extra. "
            "Install with 'pip install service-toolkit[nats]'."
        )
    if missing == "redis":
        return ModuleNotFoundError(
            "Redis helpers require the optional 'redis' extra. "
            "Install with 'pip install service-toolkit[redis]'."
        )
    if missing in {"awesome_errors", "awesome-errors"}:
        return ModuleNotFoundError(
            "App factory and error helpers require the optional 'errors' extra. "
            "Install with 'pip install service-toolkit[errors]'."
        )
    if missing == "msgspec" and module_name.startswith(".state."):
        return ModuleNotFoundError(
            "Redis-backed state helpers require the optional 'redis' extra. "
            "Install with 'pip install service-toolkit[redis]'."
        )
    if missing == "litestar":
        return ModuleNotFoundError(
            "Web helpers require Litestar. "
            "Install with 'pip install litestar[standard]'."
        )
    if missing == "grpc":
        return ModuleNotFoundError(
            "gRPC helpers require the optional 'grpc' extra. "
            "Install with 'pip install service-toolkit[grpc]'."
        )
    if missing.startswith("opentelemetry"):
        return ModuleNotFoundError(
            "Tracing helpers require the optional 'tracing' extra. "
            "Install with 'pip install service-toolkit[tracing]'."
        )
    return exc
