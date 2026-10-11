"""The live resolver caches each subject's platform bits, and fences its fetches."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from service_toolkit.security.platform_perms_resolver import PlatformPermsResolver

_PLATFORM = {"platform.audit.view": 0, "platform.staff.manage": 1}
#: The grant the tests move around: held, it sets bit 0.
_GRANT = "platform.audit.view"


def _bits(codes: list[str]) -> int:
    return sum(1 << _PLATFORM[c] for c in codes if c in _PLATFORM)


@dataclass
class _Response:
    user_id: int
    bits: int
    version: int = 1
    roles: list[str] = field(default_factory=list)
    max_role_weight: int = 0


@dataclass
class _Stub:
    responses: dict[int, _Response]
    calls: list[tuple[int, int | None]] = field(default_factory=list)

    async def ResolvePlatformPerms(self, request: Any) -> _Response:  # noqa: N802
        self.calls.append((request["user_id"], request["tenant_id"]))
        return self.responses[request["user_id"]]


def _resolver(stub: Any) -> PlatformPermsResolver:
    return PlatformPermsResolver(
        stub=stub,
        request_factory=lambda *, user_id, tenant_id: {
            "user_id": user_id,
            "tenant_id": tenant_id,
        },
    )


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


@dataclass
class _TenantStub:
    """Answers per (user, tenant): a tenant-bound grant only under its tenant."""

    grants: dict[tuple[int, int | None], list[str]]
    calls: list[tuple[int, int | None]] = field(default_factory=list)
    gate: asyncio.Event | None = None

    async def ResolvePlatformPerms(self, request: Any) -> _Response:  # noqa: N802
        key = (request["user_id"], request["tenant_id"])
        self.calls.append(key)
        if self.gate is not None:
            await self.gate.wait()
        return _Response(user_id=key[0], bits=_bits(self.grants.get(key, [])))


def test_a_global_read_does_not_answer_a_tenant_read() -> None:
    stub = _TenantStub({(1, None): [], (1, 7): [_GRANT]})
    resolver = _resolver(stub)

    async def check() -> None:
        assert not await resolver.has_code(1, _GRANT, _PLATFORM)
        assert await resolver.has_code(1, _GRANT, _PLATFORM, tenant_id=7)
        assert stub.calls == [(1, None), (1, 7)], "the tenant read fetched"
        assert not await resolver.has_code(1, _GRANT, _PLATFORM), (
            "and the global stays global"
        )

    _run(check())


def test_one_tenant_does_not_answer_for_another() -> None:
    stub = _TenantStub({(1, 7): [_GRANT], (1, 8): []})
    resolver = _resolver(stub)

    async def check() -> None:
        assert await resolver.has_code(1, _GRANT, _PLATFORM, tenant_id=7)
        assert not await resolver.has_code(1, _GRANT, _PLATFORM, tenant_id=8)
        assert stub.calls == [(1, 7), (1, 8)]
        assert await resolver.has_code(1, _GRANT, _PLATFORM, tenant_id=7)
        assert len(stub.calls) == 2, "each tenant's answer is cached under its own key"

    _run(check())


def test_invalidating_a_user_evicts_every_tenant_variant() -> None:
    stub = _TenantStub(
        {(1, None): [_GRANT], (1, 7): [_GRANT], (1, 8): [_GRANT], (2, None): [_GRANT]}
    )
    resolver = _resolver(stub)

    async def check() -> None:
        for tenant in (None, 7, 8):
            await resolver.get(1, tenant_id=tenant)
        await resolver.get(2)
        resolver.invalidate(1)
        for tenant in (None, 7, 8):
            await resolver.get(1, tenant_id=tenant)
        await resolver.get(2)
        assert stub.calls.count((1, None)) == 2
        assert stub.calls.count((1, 7)) == 2
        assert stub.calls.count((1, 8)) == 2
        assert stub.calls.count((2, None)) == 1, "another user's entry survives"

    _run(check())


def test_an_invalidation_event_evicts_every_tenant_variant() -> None:
    import json

    stub = _TenantStub({(1, None): [], (1, 7): [], (2, 9): []})
    resolver = _resolver(stub)

    async def check() -> None:
        await resolver.get(1)
        await resolver.get(1, tenant_id=7)
        await resolver.get(2, tenant_id=9)
        await resolver._handle_user_invalidation(
            SimpleNamespace(subject="auth.rbac.user.invalidated.1", data=b"")
        )
        await resolver._handle_role_invalidation(
            SimpleNamespace(
                subject="auth.rbac.role.invalidated.5",
                data=json.dumps({"data": {"user_ids": [2]}}).encode(),
            )
        )
        assert resolver._cache == {}

    _run(check())


def test_concurrent_reads_share_a_fetch_only_for_the_same_tenant() -> None:
    async def check() -> None:
        stub = _TenantStub({}, gate=asyncio.Event())
        resolver = _resolver(stub)
        reads = [
            asyncio.create_task(resolver.get(1, tenant_id=7)),
            asyncio.create_task(resolver.get(1, tenant_id=7)),
            asyncio.create_task(resolver.get(1, tenant_id=8)),
            asyncio.create_task(resolver.get(1)),
        ]
        for _ in range(5):
            await asyncio.sleep(0)
        stub.gate.set()  # type: ignore[union-attr]
        await asyncio.gather(*reads)
        assert sorted(stub.calls, key=repr) == sorted(
            [(1, 7), (1, 8), (1, None)], key=repr
        )
        assert resolver._inflight == {}

    _run(check())


def test_a_failed_fetch_leaves_no_inflight_entry_and_is_retried() -> None:
    class _Flaky:
        def __init__(self) -> None:
            self.calls = 0

        async def ResolvePlatformPerms(self, request: Any) -> _Response:  # noqa: N802
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("auth unavailable")
            return _Response(user_id=request["user_id"], bits=_bits([_GRANT]))

    stub = _Flaky()
    resolver = _resolver(stub)

    async def check() -> None:
        with pytest.raises(RuntimeError):
            await resolver.get(1, tenant_id=7)
        assert resolver._inflight == {}
        assert resolver.metrics.grpc_errors == 1
        assert await resolver.has_code(1, _GRANT, _PLATFORM, tenant_id=7)

    _run(check())


def test_max_entries_counts_subjects_and_refreshing_one_evicts_nothing() -> None:
    stub = _TenantStub({})
    resolver = PlatformPermsResolver(
        stub=stub,
        request_factory=lambda *, user_id, tenant_id: {
            "user_id": user_id,
            "tenant_id": tenant_id,
        },
        max_entries=2,
    )

    async def check() -> None:
        await resolver.get(1)
        await resolver.get(1, tenant_id=7)
        resolver._store((1, 7), resolver._cache[(1, 7)])
        assert set(resolver._cache) == {(1, None), (1, 7)}
        await resolver.get(1, tenant_id=8)
        assert len(resolver._cache) == 2
        assert (1, 8) in resolver._cache

    _run(check())


class _Gated:
    """A provider whose every call answers from the grants at call time, then
    blocks until released, so a test can invalidate while a fetch is in flight."""

    def __init__(self, grants: dict[tuple[int, int | None], list[str]]) -> None:
        self.grants = grants
        self.calls: list[tuple[int, int | None]] = []
        self.started: list[asyncio.Event] = []
        self.release: list[asyncio.Event] = []

    async def ResolvePlatformPerms(self, request: Any) -> _Response:  # noqa: N802
        key = (request["user_id"], request["tenant_id"])
        answer = list(self.grants.get(key, []))
        self.calls.append(key)
        started, release = (
            self.started[len(self.calls) - 1],
            self.release[len(self.calls) - 1],
        )
        started.set()
        await release.wait()
        return _Response(user_id=key[0], bits=_bits(answer))

    def arm(self, n: int) -> None:
        self.started = [asyncio.Event() for _ in range(n)]
        self.release = [asyncio.Event() for _ in range(n)]


def _gated_resolver(provider: _Gated) -> PlatformPermsResolver:
    return _resolver(provider)


async def _within(awaitable: Any, failure: str, seconds: float = 2.0) -> Any:
    """Await one step of a race test, failing instead of hanging.

    A regression in this machinery shows up as a step that never completes
    (a reader parked on a fetch that will never be released). Bounding every
    step turns that into a clear failure rather than a CI job timeout.
    """
    try:
        return await asyncio.wait_for(awaitable, seconds)
    except TimeoutError:
        raise AssertionError(failure) from None


async def _revoked_while_in_flight(tenant: int | None) -> None:
    provider = _Gated({(1, tenant): [_GRANT]})
    provider.arm(2)
    resolver = _gated_resolver(provider)

    stale = asyncio.create_task(resolver.get(1, tenant_id=tenant))
    await _within(
        provider.started[0].wait(), "the first fetch never reached the provider"
    )

    provider.grants[(1, tenant)] = []  # the grant is revoked...
    resolver.invalidate(1)  # ...and the user invalidated, mid-fetch
    provider.release[0].set()

    old = await _within(stale, "the original awaiter never got its answer")
    assert old.bits & 1, "the original awaiter gets its answer"
    assert (1, tenant) not in resolver._cache, "but the cache is not repopulated"

    provider.release[1].set()
    fresh = await _within(
        resolver.get(1, tenant_id=tenant),
        "a read after the invalidation never completed",
    )
    assert not fresh.bits & 1
    assert provider.calls == [(1, tenant), (1, tenant)]
    assert resolver._inflight == {}
    assert resolver._epochs == {} and resolver._running == {}


def test_an_answer_fetched_before_an_invalidation_is_not_cached() -> None:
    _run(_revoked_while_in_flight(None))


def test_an_answer_fetched_before_an_invalidation_is_not_cached_for_a_tenant() -> None:
    _run(_revoked_while_in_flight(7))


def test_a_reader_after_an_invalidation_starts_a_fresh_fetch() -> None:
    async def check() -> None:
        provider = _Gated({(1, None): [_GRANT]})
        provider.arm(2)
        resolver = _gated_resolver(provider)

        stale = asyncio.create_task(resolver.get(1))
        await _within(
            provider.started[0].wait(), "the first fetch never reached the provider"
        )
        provider.grants[(1, None)] = []
        resolver.invalidate(1)

        fresh = asyncio.create_task(resolver.get(1))
        await _within(
            provider.started[1].wait(),
            "post-invalidation reader joined the stale fetch",
        )
        assert provider.calls == [(1, None), (1, None)], (
            "it did not join the stale fetch"
        )
        fresh_task = resolver._inflight[(1, None)]

        provider.release[0].set()
        old = await _within(stale, "the stale fetch's own awaiter never got its answer")
        assert old.bits & 1
        assert resolver._inflight.get((1, None)) is fresh_task, (
            "the stale fetch's cleanup left the fresh entry alone"
        )
        assert (1, None) not in resolver._cache

        provider.release[1].set()
        new = await _within(fresh, "the fresh fetch never answered")
        assert not new.bits & 1
        assert resolver._cache[(1, None)].bits == 0
        assert resolver._epochs == {} and resolver._running == {}

    _run(check())


def test_readers_within_one_epoch_still_share_a_fetch() -> None:
    async def check() -> None:
        provider = _Gated({(1, 7): [_GRANT]})
        provider.arm(1)
        resolver = _gated_resolver(provider)

        first = asyncio.create_task(resolver.get(1, tenant_id=7))
        await _within(
            provider.started[0].wait(), "the fetch never reached the provider"
        )
        second = asyncio.create_task(resolver.get(1, tenant_id=7))
        provider.release[0].set()
        await _within(
            asyncio.gather(first, second),
            "readers of one key did not share the fetch",
        )

        assert provider.calls == [(1, 7)]
        assert (1, 7) in resolver._cache

    _run(check())


def test_an_invalidation_with_nothing_in_flight_leaves_no_epoch_behind() -> None:
    stub = _TenantStub({(1, None): [_GRANT]})
    resolver = _resolver(stub)

    async def check() -> None:
        await _within(resolver.get(1), "the read never completed")
        for uid in range(100):
            resolver.invalidate(uid)
        resolver.invalidate_many(list(range(100, 200)))
        assert resolver._epochs == {} and resolver._running == {}

    _run(check())
