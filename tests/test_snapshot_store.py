from __future__ import annotations

import asyncio

import pytest

from service_toolkit.state.redis import Keyspace
from service_toolkit.state.snapshot_store import RedisReplicaStore


def _build_store(
    *,
    local_ttl_seconds: float,
    local_max_entries: int | None = None,
) -> RedisReplicaStore[str, dict[str, int]]:
    return RedisReplicaStore[str, dict[str, int]](
        redis_enabled=False,
        keyspace=Keyspace("snapshot-test"),
        ttl_seconds=60,
        local_ttl_seconds=local_ttl_seconds,
        local_max_entries=local_max_entries,
        value_type=dict,
        key_serializer=str,
        redis_client_factory=lambda: None,
        log_name="snapshot-test",
    )


@pytest.mark.asyncio
async def test_local_entries_expire_without_redis() -> None:
    store = _build_store(local_ttl_seconds=0.05)

    await store.set("current", {"value": 1})

    assert await store.get("current") == {"value": 1}

    await asyncio.sleep(0.07)

    assert await store.get("current") is None


@pytest.mark.asyncio
async def test_local_cache_respects_max_entries() -> None:
    store = _build_store(local_ttl_seconds=10.0, local_max_entries=2)

    store.set_local("a", {"value": 1})
    store.set_local("b", {"value": 2})
    store.set_local("c", {"value": 3})

    assert await store.get_many(["a", "b", "c"]) == {
        "b": {"value": 2},
        "c": {"value": 3},
    }
