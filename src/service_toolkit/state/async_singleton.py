"""Async singleton helper for process-wide shared resources."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Generic, TypeVar, cast

T = TypeVar("T")
_UNSET = object()


class AsyncSingleton(Generic[T]):
    """Lazily initialize a single async resource per process."""

    def __init__(self, factory: Callable[[], Awaitable[T]]) -> None:
        self._factory = factory
        self._value: object = _UNSET
        self._lock = asyncio.Lock()

    async def get(self) -> T:
        """Return the shared resource, creating it on first access."""
        value = self._value
        if value is not _UNSET:
            return cast("T", value)

        async with self._lock:
            value = self._value
            if value is _UNSET:
                value = await self._factory()
                self._value = value
            return cast("T", value)

    async def reset(
        self,
        closer: Callable[[T], Awaitable[None]] | None = None,
    ) -> None:
        """Drop the cached value and optionally close it."""
        async with self._lock:
            if self._value is _UNSET:
                return
            value = self._value
            self._value = _UNSET
            if closer is not None:
                await closer(cast("T", value))


__all__ = ["AsyncSingleton"]
