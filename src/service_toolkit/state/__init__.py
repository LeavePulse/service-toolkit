"""Process-local and Redis-backed state helpers."""

from __future__ import annotations

import importlib
import sys

__all__ = [
    "AsyncSingleton",
    "CacheMode",
    "DEFAULT_REDIS_DB",
    "DEFAULT_REDIS_HOST",
    "DEFAULT_REDIS_PORT",
    "Keyspace",
    "LeaderLease",
    "LookupCache",
    "RedisClient",
    "RedisFailureMode",
    "RedisLock",
    "RedisReplicaStore",
    "RedisSettings",
    "ttl_with_jitter",
]

_EXPORT_MODULES = {
    "AsyncSingleton": ".async_singleton",
    "CacheMode": ".cache",
    "DEFAULT_REDIS_DB": ".redis",
    "DEFAULT_REDIS_HOST": ".redis",
    "DEFAULT_REDIS_PORT": ".redis",
    "Keyspace": ".redis",
    "LeaderLease": ".redis",
    "LookupCache": ".cache",
    "RedisClient": ".redis",
    "RedisFailureMode": ".cache",
    "RedisLock": ".redis",
    "RedisReplicaStore": ".snapshot_store",
    "RedisSettings": ".redis",
    "ttl_with_jitter": ".redis",
}
_SUBMODULES = {
    "async_singleton": ".async_singleton",
    "cache": ".cache",
    "redis": ".redis",
    "snapshot_store": ".snapshot_store",
}


def __getattr__(name: str):
    module_name = _EXPORT_MODULES.get(name) or _SUBMODULES.get(name)
    if module_name is None:
        raise AttributeError(name)

    try:
        module = importlib.import_module(module_name, __name__)
    except ModuleNotFoundError as exc:  # pragma: no cover - runtime guard
        if (exc.name or "") in {"redis", "msgspec"}:
            raise ModuleNotFoundError(
                "Redis-backed state helpers require the optional 'redis' extra. "
                "Install with 'pip install service-toolkit[redis]'."
            ) from exc
        if exc.name == "env_settings":
            raise ModuleNotFoundError(
                "Redis-backed state helpers require the optional 'env' extra. "
                "Install with 'pip install service-toolkit[env]'."
            ) from exc
        raise

    if name in _SUBMODULES:
        value = module
    else:
        value = getattr(module, name)
    setattr(sys.modules[__name__], name, value)
    return value


def __dir__() -> list[str]:  # pragma: no cover - reflection helper
    return sorted(set(__all__))
