from __future__ import annotations

import asyncio

import pytest

from service_toolkit.async_lookup_cache import AsyncLookupCache


@pytest.mark.asyncio
async def test_returns_cached_values_without_reloading() -> None:
    calls = 0

    async def loader() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    cache = AsyncLookupCache[str, str](success_ttl_seconds=10.0)
    first = await cache.get("key", loader)
    second = await cache.get("key", loader)

    assert first == "ok"
    assert second == "ok"
    assert calls == 1


@pytest.mark.asyncio
async def test_deduplicates_inflight_loads() -> None:
    calls = 0
    gate = asyncio.Event()

    async def loader() -> str:
        nonlocal calls
        calls += 1
        await gate.wait()
        return "shared"

    cache = AsyncLookupCache[str, str](success_ttl_seconds=10.0)

    first_task = asyncio.create_task(cache.get("same", loader))
    second_task = asyncio.create_task(cache.get("same", loader))
    await asyncio.sleep(0)
    gate.set()

    first = await first_task
    second = await second_task

    assert first == "shared"
    assert second == "shared"
    assert calls == 1


@pytest.mark.asyncio
async def test_enforces_max_concurrency_across_keys() -> None:
    concurrent = 0
    peak = 0

    async def loader(name: str) -> str:
        nonlocal concurrent, peak
        _ = name
        concurrent += 1
        peak = max(peak, concurrent)
        await asyncio.sleep(0.02)
        concurrent -= 1
        return "value"

    cache = AsyncLookupCache[str, str](
        success_ttl_seconds=10.0,
        max_concurrency=1,
    )
    await asyncio.gather(
        cache.get("a", lambda: loader("a")),
        cache.get("b", lambda: loader("b")),
    )

    assert peak == 1


@pytest.mark.asyncio
async def test_caches_empty_results_with_dedicated_ttl() -> None:
    calls = 0

    async def loader() -> str:
        nonlocal calls
        calls += 1
        return ""

    cache = AsyncLookupCache[str, str](
        success_ttl_seconds=10.0,
        empty_ttl_seconds=0.05,
        is_empty=lambda value: value == "",
    )
    await cache.get("empty", loader)
    await cache.get("empty", loader)
    await asyncio.sleep(0.07)
    await cache.get("empty", loader)

    assert calls == 2

