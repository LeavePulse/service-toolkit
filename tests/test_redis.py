from __future__ import annotations

import asyncio

import pytest

from service_toolkit.redis import Keyspace, RedisCache, RedisLock, ttl_with_jitter


class FakeRedis:
    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}

    async def get(self, key: str) -> bytes | None:
        return self._store.get(key)

    async def set(
        self,
        key: str,
        value: bytes | str,
        *,
        ex: int | None = None,
        nx: bool = False,
        px: int | None = None,
    ) -> bool | None:
        _ = (ex, px)
        if isinstance(value, str):
            value = value.encode("utf-8")
        if nx and key in self._store:
            return None
        self._store[key] = value
        return True

    async def delete(self, key: str) -> int:
        if key in self._store:
            del self._store[key]
            return 1
        return 0

    async def eval(self, script: str, num_keys: int, key: str, token: str) -> int:
        _ = (script, num_keys)
        current = self._store.get(key)
        if current == token.encode("utf-8"):
            del self._store[key]
            return 1
        return 0


def test_keyspace_normalizes_separators() -> None:
    ks = Keyspace("cache:")
    assert ks.key(":foo") == "cache:foo"
    assert ks.key("foo", "bar") == "cache:foo:bar"

    ks2 = Keyspace("cache::")
    assert ks2.key("::foo::") == "cache:foo"


def test_ttl_with_jitter_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("service_toolkit.redis.random.uniform", lambda a, b: b)
    assert ttl_with_jitter(100, ratio=0.1) == 90
    assert ttl_with_jitter(1, ratio=0.9) == 1
    assert ttl_with_jitter(0, ratio=0.5) == 0


@pytest.mark.asyncio
async def test_redis_lock_acquire_release() -> None:
    client = FakeRedis()
    lock1 = RedisLock(client, "locks:test", ttl_seconds=1.0)
    lock2 = RedisLock(client, "locks:test", ttl_seconds=1.0)

    assert await lock1.acquire() is True
    assert await lock2.acquire() is False
    assert await lock1.release() is True
    assert await lock2.acquire() is True


@pytest.mark.asyncio
async def test_cache_get_or_set_json() -> None:
    client = FakeRedis()
    cache = RedisCache(client, keyspace=Keyspace("cache"), default_ttl_seconds=60)
    calls = 0

    async def producer() -> dict[str, int]:
        nonlocal calls
        calls += 1
        return {"value": calls}

    value1 = await cache.get_or_set_json("foo", producer, ttl_seconds=60)
    value2 = await cache.get_or_set_json("foo", producer, ttl_seconds=60)

    assert value1 == {"value": 1}
    assert value2 == {"value": 1}
    assert calls == 1


@pytest.mark.asyncio
async def test_lock_wait_acquires_after_release() -> None:
    client = FakeRedis()
    lock1 = RedisLock(client, "locks:wait", ttl_seconds=1.0)
    lock2 = RedisLock(client, "locks:wait", ttl_seconds=1.0)

    assert await lock1.acquire() is True

    async def releaser() -> None:
        await asyncio.sleep(0.05)
        await lock1.release()

    task = asyncio.create_task(releaser())
    try:
        assert (
            await lock2.acquire_with_wait(timeout_seconds=1.0, initial_delay_seconds=0.01)
            is True
        )
    finally:
        task.cancel()

