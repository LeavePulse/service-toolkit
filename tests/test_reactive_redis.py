from __future__ import annotations

import os

import pytest

from service_toolkit.reactive import PendingEffect, Record, RedisStore, RedisTimers


class FakeHashes:
    def __init__(self) -> None:
        self.data: dict[str, dict[str, bytes]] = {}

    async def hget(self, key: str, field: str) -> bytes | None:
        return self.data.get(key, {}).get(field)

    async def hset(self, key: str, field: str, value: bytes) -> None:
        self.data.setdefault(key, {})[field] = value

    async def hdel(self, key: str, field: str) -> None:
        self.data.get(key, {}).pop(field, None)

    async def hkeys(self, key: str) -> list[bytes]:
        return [k.encode() for k in self.data.get(key, {})]


@pytest.mark.asyncio
async def test_store_round_trips_a_record_and_forgets_a_broken_one() -> None:
    client = FakeHashes()
    store = RedisStore(client, prefix="t")
    record = Record(holds=True, since=5.0, epoch=2, pending=(PendingEffect("r:member:1:2:enter", "enter"),))
    await store.put("r", "member:1", record)
    assert await store.get("r", "member:1") == record
    assert await store.entities("r") == ["member:1"]
    client.data["t:rule:r"]["member:2"] = b"{not json"
    assert await store.get("r", "member:2") is None
    await store.delete("r", "member:1")
    assert await store.get("r", "member:1") is None


REDIS_URL = os.environ.get("REACTIVE_REDIS_URL")


@pytest.mark.asyncio
@pytest.mark.skipif(not REDIS_URL, reason="set REACTIVE_REDIS_URL to run against a real Redis")
async def test_timers_keep_the_earlier_due_and_hand_each_key_out_once() -> None:
    import redis.asyncio as redis

    client = redis.from_url(REDIS_URL or "")
    timers = RedisTimers(client, prefix="reactive-test")
    try:
        await client.delete("reactive-test:timers")
        await timers.schedule("r|member:1", 100.0)
        await timers.schedule("r|member:1", 200.0)  # later: ignored
        await timers.schedule("r|member:2", 150.0)
        await timers.schedule("r|member:3", 500.0)
        assert await timers.take_due(160.0) == ["r|member:1", "r|member:2"]
        assert await timers.take_due(160.0) == []
        await timers.cancel("r|member:3")
        assert await timers.take_due(1000.0) == []
    finally:
        await client.delete("reactive-test:timers")
        await client.aclose()
