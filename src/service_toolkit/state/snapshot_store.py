"""Shared L1/L2 snapshot store with optional Redis replication."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable
from typing import Any, Generic, TypeVar

import msgspec

from .redis import Keyspace, RedisClient

K = TypeVar("K")
V = TypeVar("V")

logger = logging.getLogger(__name__)


class RedisReplicaStore(Generic[K, V]):
    """Keep snapshots in local memory and optionally replicate them via Redis."""

    def __init__(
        self,
        *,
        redis_enabled: bool,
        keyspace: Keyspace,
        ttl_seconds: int,
        value_type: Any,
        key_serializer: Callable[[K], str],
        redis_client_factory: Callable[[], RedisClient],
        log_name: str,
    ) -> None:
        self._local: dict[K, V] = {}
        self._redis: RedisClient | None = None
        self._connect_lock = asyncio.Lock()
        self._redis_enabled = bool(redis_enabled)
        self._keyspace = keyspace
        self._ttl_seconds = max(1, int(ttl_seconds))
        self._value_type = value_type
        self._key_serializer = key_serializer
        self._redis_client_factory = redis_client_factory
        self._log_name = log_name

    async def start(self) -> None:
        if not self._redis_enabled:
            return
        if self._redis is not None:
            return
        async with self._connect_lock:
            if self._redis is not None:
                return
            client = self._redis_client_factory()
            await client.connect()
            self._redis = client

    async def stop(self) -> None:
        client = self._redis
        self._redis = None
        if client is not None:
            await client.aclose()

    def set_local(self, key: K, value: V) -> None:
        self._local[key] = value

    def set_local_many(self, values: dict[K, V]) -> None:
        self._local.update(values)

    async def set(self, key: K, value: V) -> None:
        await self.set_many({key: value})

    async def set_many(self, values: dict[K, V]) -> None:
        if not values:
            return

        self._local.update(values)
        if not self._redis_enabled:
            return

        await self.start()
        if self._redis is None:
            return

        try:
            async with self._redis.client.pipeline(transaction=False) as pipeline:
                for key, value in values.items():
                    pipeline.set(
                        self._redis_key(key),
                        msgspec.json.encode(value),
                        ex=self._ttl_seconds,
                    )
                await pipeline.execute()
        except Exception:  # pragma: no cover - defensive logging
            logger.exception("Failed to persist %s snapshots", self._log_name)

    async def get(self, key: K) -> V | None:
        result = await self.get_many([key])
        return result.get(key)

    async def get_many(self, keys: Iterable[K]) -> dict[K, V]:
        requested = list(keys)
        if not requested:
            return {}

        result: dict[K, V] = {}
        missing: list[K] = []
        for key in requested:
            cached = self._local.get(key)
            if cached is not None:
                result[key] = cached
            else:
                missing.append(key)

        if missing and self._redis_enabled:
            await self.start()
            if self._redis is not None:
                redis_keys = [self._redis_key(key) for key in missing]
                try:
                    values = await self._redis.client.mget(redis_keys)
                except Exception:  # pragma: no cover - defensive logging
                    logger.exception("Failed to fetch %s snapshots", self._log_name)
                else:
                    decoded: dict[K, V] = {}
                    for key, raw in zip(missing, values, strict=False):
                        if not raw:
                            continue
                        try:
                            decoded[key] = msgspec.json.decode(raw, type=self._value_type)
                        except Exception:  # pragma: no cover - malformed entry
                            continue
                    self._local.update(decoded)
                    result.update(decoded)

        return result

    def _redis_key(self, key: K) -> str:
        return self._keyspace.key(self._key_serializer(key))


__all__ = ["RedisReplicaStore"]
