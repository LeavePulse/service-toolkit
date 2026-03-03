from __future__ import annotations

import asyncio

import pytest

from service_toolkit.async_singleton import AsyncSingleton


@pytest.mark.asyncio
async def test_async_singleton_initializes_once() -> None:
    calls = 0

    async def factory() -> int:
        nonlocal calls
        await asyncio.sleep(0)
        calls += 1
        return 7

    singleton = AsyncSingleton(factory)
    results = await asyncio.gather(*[singleton.get() for _ in range(5)])

    assert results == [7, 7, 7, 7, 7]
    assert calls == 1


@pytest.mark.asyncio
async def test_async_singleton_reset_runs_closer() -> None:
    closed: list[int] = []

    async def factory() -> int:
        return 11

    async def closer(value: int) -> None:
        closed.append(value)

    singleton = AsyncSingleton(factory)
    assert await singleton.get() == 11

    await singleton.reset(closer)

    assert closed == [11]
