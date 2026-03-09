"""Unified cache primitives for local, Redis, and hybrid lookup workflows."""

from __future__ import annotations

import asyncio
import json
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable
from enum import StrEnum
from time import monotonic
from typing import Any, Generic, TypeVar, cast

from .redis import Keyspace, RedisLock, ttl_with_jitter

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")

_MISSING = object()


class CacheMode(StrEnum):
    LOCAL = "local"
    REDIS = "redis"
    HYBRID = "hybrid"


class RedisFailureMode(StrEnum):
    LOCAL_FALLBACK = "local_fallback"
    BYPASS = "bypass"
    RAISE = "raise"


def _default_encode(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _default_decode(data: bytes) -> Any:
    return json.loads(data.decode("utf-8"))


def _ttl_with_jitter_seconds(
    ttl_seconds: float,
    *,
    ratio: float = 0.0,
    max_jitter_seconds: float | None = None,
) -> float:
    if ttl_seconds <= 0:
        return ttl_seconds
    if ratio <= 0:
        return ttl_seconds
    jitter_limit = max(0.0, ttl_seconds * ratio)
    if max_jitter_seconds is not None:
        jitter_limit = min(jitter_limit, float(max_jitter_seconds))
    jitter = __import__("random").uniform(0.0, jitter_limit)
    return max(0.0, ttl_seconds - jitter)


class LookupCache(Generic[K, V]):
    """Unified cache for local-only, Redis-only, and hybrid lookup flows."""

    def __init__(
        self,
        *,
        mode: CacheMode = CacheMode.HYBRID,
        local_ttl_seconds: float = 300.0,
        empty_ttl_seconds: float | None = None,
        is_empty: Callable[[V], bool] | None = None,
        max_entries: int = 2048,
        max_concurrency: int | None = None,
        local_ttl_jitter_ratio: float = 0.0,
        local_ttl_jitter_max_seconds: float | None = None,
        redis_client: Any | None = None,
        redis_keyspace: Keyspace | None = None,
        redis_ttl_seconds: int | None = None,
        redis_empty_ttl_seconds: int | None = None,
        redis_lock_ttl_seconds: float = 10.0,
        redis_wait_timeout_seconds: float = 2.0,
        redis_ttl_jitter_ratio: float = 0.0,
        redis_ttl_jitter_max_seconds: int | None = None,
        redis_failure_mode: RedisFailureMode = RedisFailureMode.LOCAL_FALLBACK,
        encode: Callable[[V], bytes] | None = None,
        decode: Callable[[bytes], V] | None = None,
        key_serializer: Callable[[K], str] | None = None,
    ) -> None:
        self._mode = CacheMode(mode)
        self._local_ttl_seconds = max(0.0, float(local_ttl_seconds))
        self._empty_ttl_seconds = (
            max(0.0, float(empty_ttl_seconds))
            if empty_ttl_seconds is not None
            else self._local_ttl_seconds
        )
        self._is_empty = is_empty
        self._max_entries = max(1, int(max_entries))
        self._local_ttl_jitter_ratio = max(0.0, float(local_ttl_jitter_ratio))
        self._local_ttl_jitter_max_seconds = (
            None
            if local_ttl_jitter_max_seconds is None
            else max(0.0, float(local_ttl_jitter_max_seconds))
        )
        self._redis_client = redis_client
        self._redis_keyspace = redis_keyspace
        self._redis_ttl_seconds = (
            None if redis_ttl_seconds is None else max(0, int(redis_ttl_seconds))
        )
        self._redis_empty_ttl_seconds = (
            self._redis_ttl_seconds
            if redis_empty_ttl_seconds is None
            else max(0, int(redis_empty_ttl_seconds))
        )
        self._redis_lock_ttl_seconds = max(1.0, float(redis_lock_ttl_seconds))
        self._redis_wait_timeout_seconds = max(0.0, float(redis_wait_timeout_seconds))
        self._redis_ttl_jitter_ratio = max(0.0, float(redis_ttl_jitter_ratio))
        self._redis_ttl_jitter_max_seconds = redis_ttl_jitter_max_seconds
        self._redis_failure_mode = RedisFailureMode(redis_failure_mode)
        self._encode = encode or _default_encode
        self._decode = decode or _default_decode
        self._key_serializer = key_serializer or (lambda key: str(key))

        self._cache: OrderedDict[K, tuple[V, float]] = OrderedDict()
        self._inflight: dict[K, asyncio.Task[V]] = {}
        self._lock = asyncio.Lock()
        self._semaphore = (
            asyncio.Semaphore(max(1, int(max_concurrency)))
            if max_concurrency is not None
            else None
        )

        if self._mode in {CacheMode.REDIS, CacheMode.HYBRID}:
            if self._redis_client is None:
                raise ValueError("redis_client is required for redis or hybrid caches")
            if self._redis_keyspace is None:
                raise ValueError("redis_keyspace is required for redis or hybrid caches")
            if self._redis_ttl_seconds is None:
                raise ValueError("redis_ttl_seconds is required for redis or hybrid caches")

        if (
            self._redis_failure_mode is RedisFailureMode.LOCAL_FALLBACK
            and not self.local_enabled
            and self.redis_enabled
        ):
            raise ValueError(
                "LOCAL_FALLBACK requires a local cache layer when Redis is enabled"
            )

    @property
    def mode(self) -> CacheMode:
        return self._mode

    @property
    def local_enabled(self) -> bool:
        return (
            self._mode in {CacheMode.LOCAL, CacheMode.HYBRID}
            and self._local_ttl_seconds > 0
        )

    @property
    def redis_enabled(self) -> bool:
        return self._mode in {CacheMode.REDIS, CacheMode.HYBRID}

    @property
    def local_size(self) -> int:
        return len(self._cache)

    @property
    def inflight_size(self) -> int:
        return len(self._inflight)

    async def get(self, key: K, loader: Callable[[], Awaitable[V]]) -> V:
        cached = await self._get_local(key)
        if cached is not _MISSING:
            return cast("V", cached)

        async with self._lock:
            cached = self._get_local_unlocked(key)
            if cached is not _MISSING:
                return cast("V", cached)

            task = self._inflight.get(key)
            created = False
            if task is None:
                task = asyncio.create_task(self._load_uncached(key, loader))
                self._inflight[key] = task
                created = True

        try:
            return await task
        finally:
            if created:
                async with self._lock:
                    current = self._inflight.get(key)
                    if current is task:
                        self._inflight.pop(key, None)

    async def get_cached(self, key: K) -> V | None:
        cached = await self._get_local(key)
        if cached is not _MISSING:
            return cast("V", cached)

        cached = await self._get_redis(key)
        if cached is _MISSING:
            return None
        value = cast("V", cached)
        await self._store_local(key, value)
        return value

    async def set(self, key: K, value: V) -> None:
        await self._store_local(key, value)
        await self._set_redis(key, value)

    async def delete(self, key: K) -> None:
        await self.invalidate(key)

    async def invalidate(self, key: K) -> None:
        async with self._lock:
            self._cache.pop(key, None)
        if not self.redis_enabled:
            return
        client = self._require_redis_client()
        try:
            await client.delete(self._redis_key(key))
        except Exception:
            if self._redis_failure_mode is RedisFailureMode.RAISE:
                raise

    async def clear_local(self) -> None:
        async with self._lock:
            self._cache.clear()

    async def _load_uncached(self, key: K, loader: Callable[[], Awaitable[V]]) -> V:
        cached = await self._get_redis(key)
        if cached is not _MISSING:
            value = cast("V", cached)
            await self._store_local(key, value)
            return value

        if not self.redis_enabled:
            value = await self._run_loader(loader)
            await self._store_local(key, value)
            return value

        lock = RedisLock(
            self._require_redis_client(),
            self._redis_lock_key(key),
            ttl_seconds=self._redis_lock_ttl_seconds,
        )
        try:
            acquired = await lock.acquire()
        except Exception:
            return await self._load_without_redis(loader, key)

        if acquired:
            try:
                cached = await self._get_redis(key)
                if cached is not _MISSING:
                    value = cast("V", cached)
                    await self._store_local(key, value)
                    return value

                value = await self._run_loader(loader)
                await self._store_local(key, value)
                await self._set_redis(key, value)
                return value
            finally:
                try:
                    await lock.release()
                except Exception:
                    if self._redis_failure_mode is RedisFailureMode.RAISE:
                        raise

        cached = await self._wait_for_redis_fill(key)
        if cached is not _MISSING:
            value = cast("V", cached)
            await self._store_local(key, value)
            return value

        value = await self._run_loader(loader)
        await self._store_local(key, value)
        await self._set_redis(key, value)
        return value

    async def _load_without_redis(
        self,
        loader: Callable[[], Awaitable[V]],
        key: K,
    ) -> V:
        if self._redis_failure_mode is RedisFailureMode.RAISE:
            cached = await self._get_redis(key)
            if cached is _MISSING:
                msg = f"Redis cache unavailable for key {self._key_serializer(key)}"
                raise RuntimeError(msg)
            return cast("V", cached)

        value = await self._run_loader(loader)
        if self.local_enabled:
            await self._store_local(key, value)
        return value

    async def _wait_for_redis_fill(self, key: K) -> V | object:
        if self._redis_wait_timeout_seconds <= 0:
            return _MISSING

        deadline = monotonic() + self._redis_wait_timeout_seconds
        delay = 0.05
        while monotonic() < deadline:
            await asyncio.sleep(delay)
            cached = await self._get_redis(key)
            if cached is not _MISSING:
                return cached
            delay = min(0.5, delay * 1.5)
        return _MISSING

    async def _run_loader(self, loader: Callable[[], Awaitable[V]]) -> V:
        if self._semaphore is None:
            return await loader()
        async with self._semaphore:
            return await loader()

    async def _get_local(self, key: K) -> V | object:
        async with self._lock:
            return self._get_local_unlocked(key)

    def _get_local_unlocked(self, key: K) -> V | object:
        if not self.local_enabled:
            return _MISSING

        cached = self._cache.get(key)
        if cached is None:
            return _MISSING

        value, expires_at = cached
        if monotonic() >= expires_at:
            self._cache.pop(key, None)
            return _MISSING

        self._cache.move_to_end(key)
        return value

    async def _store_local(self, key: K, value: V) -> None:
        if not self.local_enabled:
            return

        ttl_seconds = self._effective_local_ttl(value)
        if ttl_seconds <= 0:
            return

        expires_at = monotonic() + ttl_seconds
        async with self._lock:
            self._cache[key] = (value, expires_at)
            self._cache.move_to_end(key)
            while len(self._cache) > self._max_entries:
                self._cache.popitem(last=False)

    async def _get_redis(self, key: K) -> V | object:
        if not self.redis_enabled:
            return _MISSING
        client = self._require_redis_client()
        try:
            data = await client.get(self._redis_key(key))
        except Exception:
            if self._redis_failure_mode is RedisFailureMode.RAISE:
                raise
            return _MISSING
        if data is None:
            return _MISSING
        return self._decode(data)

    async def _set_redis(self, key: K, value: V) -> None:
        if not self.redis_enabled:
            return

        ttl_seconds = self._effective_redis_ttl(value)
        if ttl_seconds <= 0:
            return

        client = self._require_redis_client()
        try:
            await client.set(
                self._redis_key(key),
                self._encode(value),
                ex=ttl_seconds,
            )
        except Exception:
            if self._redis_failure_mode is RedisFailureMode.RAISE:
                raise

    def _effective_local_ttl(self, value: V) -> float:
        ttl = (
            self._empty_ttl_seconds
            if self._is_empty_value(value)
            else self._local_ttl_seconds
        )
        return _ttl_with_jitter_seconds(
            ttl,
            ratio=self._local_ttl_jitter_ratio,
            max_jitter_seconds=self._local_ttl_jitter_max_seconds,
        )

    def _effective_redis_ttl(self, value: V) -> int:
        ttl = (
            self._redis_empty_ttl_seconds
            if self._is_empty_value(value)
            else self._redis_ttl_seconds
        )
        if ttl is None:
            return 0
        return ttl_with_jitter(
            int(ttl),
            ratio=self._redis_ttl_jitter_ratio,
            max_jitter_seconds=self._redis_ttl_jitter_max_seconds,
        )

    def _is_empty_value(self, value: V) -> bool:
        if self._is_empty is None:
            return False
        return bool(self._is_empty(value))

    def _redis_key(self, key: K) -> str:
        return self._require_redis_keyspace().key(self._key_serializer(key))

    def _redis_lock_key(self, key: K) -> str:
        return self._require_redis_keyspace().key(self._key_serializer(key), "lock")

    def _require_redis_client(self) -> Any:
        if self._redis_client is None:
            raise RuntimeError("Redis client is not configured for this cache")
        return self._redis_client

    def _require_redis_keyspace(self) -> Keyspace:
        if self._redis_keyspace is None:
            raise RuntimeError("Redis keyspace is not configured for this cache")
        return self._redis_keyspace


__all__ = [
    "CacheMode",
    "LookupCache",
    "RedisFailureMode",
]
