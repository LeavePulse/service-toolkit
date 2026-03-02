"""Async lookup cache with in-flight deduplication and concurrency limits."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable
from time import monotonic
from typing import Generic, TypeVar

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


class AsyncLookupCache(Generic[K, V]):
    """Cache async lookup results by key.

    Features:
    - in-memory TTL cache with LRU eviction
    - in-flight deduplication (same key -> one running loader task)
    - optional global concurrency limit for loaders
    - optional separate TTL for "empty" results
    """

    def __init__(
        self,
        *,
        success_ttl_seconds: float,
        max_entries: int = 2048,
        empty_ttl_seconds: float | None = None,
        is_empty: Callable[[V], bool] | None = None,
        max_concurrency: int | None = None,
    ) -> None:
        self._success_ttl = max(0.0, float(success_ttl_seconds))
        self._empty_ttl = (
            max(0.0, float(empty_ttl_seconds))
            if empty_ttl_seconds is not None
            else self._success_ttl
        )
        self._is_empty = is_empty
        self._max_entries = max(1, int(max_entries))

        self._lock = asyncio.Lock()
        self._cache: OrderedDict[K, tuple[V, float]] = OrderedDict()
        self._inflight: dict[K, asyncio.Task[V]] = {}
        self._semaphore = (
            asyncio.Semaphore(max(1, int(max_concurrency)))
            if max_concurrency is not None
            else None
        )

    async def get(self, key: K, loader: Callable[[], Awaitable[V]]) -> V:
        """Return cached value or load/cache a new one."""
        now = monotonic()
        async with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                value, expires_at = cached
                if now < expires_at:
                    self._cache.move_to_end(key)
                    return value
                self._cache.pop(key, None)

            task = self._inflight.get(key)
            created = False
            if task is None:
                task = asyncio.create_task(self._run_loader(loader))
                self._inflight[key] = task
                created = True

        try:
            value = await task
        finally:
            if created:
                async with self._lock:
                    current = self._inflight.get(key)
                    if current is task:
                        self._inflight.pop(key, None)

        ttl_seconds = self._select_ttl(value)
        if ttl_seconds <= 0:
            return value

        expires_at = monotonic() + ttl_seconds
        async with self._lock:
            self._cache[key] = (value, expires_at)
            self._cache.move_to_end(key)
            while len(self._cache) > self._max_entries:
                self._cache.popitem(last=False)
        return value

    async def invalidate(self, key: K) -> None:
        """Remove key from cache."""
        async with self._lock:
            self._cache.pop(key, None)

    async def clear(self) -> None:
        """Clear cached values."""
        async with self._lock:
            self._cache.clear()

    def _select_ttl(self, value: V) -> float:
        if self._is_empty is not None and self._is_empty(value):
            return self._empty_ttl
        return self._success_ttl

    async def _run_loader(self, loader: Callable[[], Awaitable[V]]) -> V:
        if self._semaphore is None:
            return await loader()

        async with self._semaphore:
            return await loader()

