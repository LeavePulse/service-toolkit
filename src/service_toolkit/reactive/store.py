"""What the engine remembers per rule and entity, and when to look again.

The record is bookkeeping, not business data: losing it is survivable. The
next evaluation of an entity with no record is silent (see ``engine``), so a
lost store never replays enters; converge is idempotent and simply runs again.
What a lost store cannot give back is an ``on_exit`` that happened while it
was gone, which is why nothing that bills or punishes may hang on an exit
alone.

Two stores and two timer queues: in memory, for tests and single-process
tools, and Redis, for services. Keys go through the toolkit's ``Keyspace``.
"""

from __future__ import annotations

import time
from typing import Any, Protocol

import msgspec

__all__ = [
    "MemoryStore",
    "MemoryTimers",
    "PendingEffect",
    "Record",
    "RedisStore",
    "RedisTimers",
    "Timers",
    "TransitionStore",
]


class PendingEffect(msgspec.Struct, frozen=True):
    """An edge effect that has not finished: retried with the same id."""

    id: str
    kind: str  # "enter" | "exit"
    attempts: int = 0


class Record(msgspec.Struct):
    """One rule's memory of one entity."""

    # None until the first evaluation that was not UNKNOWN.
    holds: bool | None = None
    since: float = 0.0
    # Bumps on every enter; part of the transition id.
    epoch: int = 0
    # The newest facts version applied; older views are dropped.
    version: int | None = None
    # held_for: when the inner requirement started holding, and when data went
    # missing (UNKNOWN pauses the clock rather than counting the gap).
    run_since: float | None = None
    paused_at: float | None = None
    # settle: a change seen but not yet committed, and when to commit it.
    candidate: bool | None = None
    candidate_due: float | None = None
    pending: tuple[PendingEffect, ...] = ()
    # converge retries after a failed apply or read-back.
    attempts: int = 0


_encoder = msgspec.json.Encoder()
_decoder = msgspec.json.Decoder(Record)


class TransitionStore(Protocol):
    async def get(self, rule: str, entity: str) -> Record | None: ...

    async def put(self, rule: str, entity: str, record: Record) -> None: ...

    async def delete(self, rule: str, entity: str) -> None: ...

    async def entities(self, rule: str) -> list[str]: ...


class Timers(Protocol):
    """Due re-evaluations. A key is ``"<rule>|<entity>"``."""

    async def schedule(self, key: str, due: float) -> None: ...

    async def cancel(self, key: str) -> None: ...

    async def take_due(self, now: float, limit: int = 100) -> list[str]: ...


class MemoryStore:
    def __init__(self) -> None:
        self._data: dict[str, dict[str, Record]] = {}

    async def get(self, rule: str, entity: str) -> Record | None:
        record = self._data.get(rule, {}).get(entity)
        return msgspec.structs.replace(record) if record else None

    async def put(self, rule: str, entity: str, record: Record) -> None:
        self._data.setdefault(rule, {})[entity] = msgspec.structs.replace(record)

    async def delete(self, rule: str, entity: str) -> None:
        self._data.get(rule, {}).pop(entity, None)

    async def entities(self, rule: str) -> list[str]:
        return list(self._data.get(rule, {}))


class MemoryTimers:
    def __init__(self) -> None:
        self._due: dict[str, float] = {}

    async def schedule(self, key: str, due: float) -> None:
        # The earlier of two wins: a later request must not postpone a check
        # another part of the rule already needs sooner.
        current = self._due.get(key)
        self._due[key] = due if current is None else min(current, due)

    async def cancel(self, key: str) -> None:
        self._due.pop(key, None)

    async def take_due(self, now: float, limit: int = 100) -> list[str]:
        ready = sorted((due, key) for key, due in self._due.items() if due <= now)[:limit]
        for _, key in ready:
            self._due.pop(key, None)
        return [key for _, key in ready]


class RedisStore:
    """A hash per rule (``<prefix>:rule:<name>``), a field per entity."""

    def __init__(self, client: Any, prefix: str = "reactive") -> None:
        from service_toolkit.state.redis import Keyspace

        self._client = client
        self._keys = Keyspace(prefix)

    def _key(self, rule: str) -> str:
        return self._keys.key("rule", rule)

    async def get(self, rule: str, entity: str) -> Record | None:
        raw = await self._client.hget(self._key(rule), entity)
        if raw is None:
            return None
        try:
            return _decoder.decode(raw)
        except msgspec.DecodeError:
            # A record from another shape of this code is treated as missing:
            # the next evaluation is silent and writes a fresh one.
            return None

    async def put(self, rule: str, entity: str, record: Record) -> None:
        await self._client.hset(self._key(rule), entity, _encoder.encode(record))

    async def delete(self, rule: str, entity: str) -> None:
        await self._client.hdel(self._key(rule), entity)

    async def entities(self, rule: str) -> list[str]:
        keys = await self._client.hkeys(self._key(rule))
        return [k.decode() if isinstance(k, bytes) else str(k) for k in keys]


_TAKE_DUE = """
local due = redis.call('zrangebyscore', KEYS[1], '-inf', ARGV[1], 'LIMIT', 0, ARGV[2])
if #due > 0 then redis.call('zrem', KEYS[1], unpack(due)) end
return due
""".strip()

_SCHEDULE_EARLIER = """
local current = redis.call('zscore', KEYS[1], ARGV[2])
if (not current) or tonumber(ARGV[1]) < tonumber(current) then
  redis.call('zadd', KEYS[1], ARGV[1], ARGV[2])
end
return 1
""".strip()


class RedisTimers:
    """A sorted set, due time as score. Taking due keys removes them atomically,
    so two leaders in a handover cannot both act on one timer."""

    def __init__(self, client: Any, prefix: str = "reactive") -> None:
        from service_toolkit.state.redis import Keyspace

        self._client = client
        self._key = Keyspace(prefix).key("timers")

    async def schedule(self, key: str, due: float) -> None:
        await self._client.eval(_SCHEDULE_EARLIER, 1, self._key, due, key)

    async def cancel(self, key: str) -> None:
        await self._client.zrem(self._key, key)

    async def take_due(self, now: float | None = None, limit: int = 100) -> list[str]:
        moment = time.time() if now is None else now
        keys = await self._client.eval(_TAKE_DUE, 1, self._key, moment, limit)
        return [k.decode() if isinstance(k, bytes) else str(k) for k in keys or []]
