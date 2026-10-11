"""The live resolver answers platform bits and exact scopes, and never mixes them."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from service_toolkit.security.platform_perms_resolver import PlatformPermsResolver

# A platform space and a second catalog that reuses its indices, which is
# exactly what made dcim.view read as platform.audit.view.
_PLATFORM = {"platform.audit.view": 0, "platform.staff.manage": 1}
_OTHER = {"dcim.view": 0, "dcim.manage": 1}


@dataclass
class _Response:
    user_id: int
    bits: int
    version: int = 1
    roles: list[str] = field(default_factory=list)
    max_role_weight: int = 0
    scopes: list[str] = field(default_factory=list)


@dataclass
class _Stub:
    responses: dict[int, _Response]
    calls: list[tuple[int, int | None]] = field(default_factory=list)

    async def ResolvePlatformPerms(self, request: Any) -> _Response:  # noqa: N802
        self.calls.append((request["user_id"], request["tenant_id"]))
        return self.responses[request["user_id"]]


def _resolver(
    stub: Any, *, platform: dict[str, int] | None = _PLATFORM
) -> PlatformPermsResolver:
    return PlatformPermsResolver(
        stub=stub,
        request_factory=lambda *, user_id, tenant_id: {
            "user_id": user_id,
            "tenant_id": tenant_id,
        },
        platform_bit_mapping=platform,
    )


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_a_platform_holder_is_not_granted_a_code_of_another_catalog() -> None:
    stub = _Stub(
        {
            1: _Response(
                user_id=1,
                bits=0b11,
                scopes=["platform.audit.view", "platform.staff.manage"],
            )
        }
    )
    resolver = _resolver(stub)

    async def check() -> None:
        assert await resolver.has_code(1, "platform.audit.view", _PLATFORM)
        assert await resolver.has_code(1, "platform.staff.manage", _PLATFORM)
        assert not await resolver.has_scope(1, "dcim.view")
        assert not await resolver.has_scope(1, "dcim.manage")
        with pytest.raises(ValueError, match="has_scope"):
            await resolver.has_code(1, "dcim.view", _OTHER)

    _run(check())


def test_a_scope_grants_exactly_its_own_code() -> None:
    stub = _Stub({2: _Response(user_id=2, bits=0, scopes=["dcim.view"])})
    resolver = _resolver(stub)

    async def check() -> None:
        assert await resolver.has_scope(2, "dcim.view")
        assert not await resolver.has_scope(2, "dcim.manage")
        assert not await resolver.has_scope(2, "dcim.vie")
        assert not await resolver.has_scope(2, "")
        assert not await resolver.has_code(2, "platform.audit.view", _PLATFORM)

    _run(check())


def test_unknown_codes_and_providers_without_scopes_deny() -> None:
    class _Old:
        """A provider built before scopes existed: the field is simply absent."""

        user_id = 3
        bits = 0b1
        version = 1
        roles: list[str] = []
        max_role_weight = 0

    class _OldStub:
        async def ResolvePlatformPerms(self, request: Any) -> _Old:  # noqa: N802
            return _Old()

    resolver = _resolver(_OldStub())

    async def check() -> None:
        assert not await resolver.has_scope(3, "dcim.view")
        assert not await resolver.has_scope(3, "infra.transport.authority")
        assert await resolver.has_code(3, "platform.audit.view", _PLATFORM)

    _run(check())


def test_scopes_ride_the_bits_cache_and_its_invalidation() -> None:
    stub = _Stub({4: _Response(user_id=4, bits=0, scopes=["dcim.manage"])})
    resolver = _resolver(stub)

    async def check() -> None:
        assert await resolver.has_scope(4, "dcim.manage")
        assert await resolver.has_scope(4, "dcim.manage")
        assert len(stub.calls) == 1, "the second answer comes from the cache"
        stub.responses[4] = _Response(user_id=4, bits=0, scopes=[])
        assert await resolver.has_scope(4, "dcim.manage"), "still cached"
        resolver.invalidate(4)
        assert not await resolver.has_scope(4, "dcim.manage")
        assert len(stub.calls) == 2

    _run(check())


def test_the_tenant_reaches_the_provider_exactly_as_for_bits() -> None:
    stub = _Stub({5: _Response(user_id=5, bits=0, scopes=["colo.scan"])})
    resolver = _resolver(stub)

    async def check() -> None:
        assert await resolver.has_scope(5, "colo.scan", tenant_id=77)
        resolver.invalidate(5)
        await resolver.bits(5, tenant_id=77)
        assert stub.calls == [(5, 77), (5, 77)]

    _run(check())


def test_a_resolver_built_without_a_platform_catalog_keeps_the_old_contract() -> None:
    stub = _Stub({6: _Response(user_id=6, bits=0b1)})
    resolver = _resolver(stub, platform=None)

    async def check() -> None:
        assert await resolver.has_code(6, "platform.audit.view", _PLATFORM)

    _run(check())


@dataclass
class _TenantStub:
    """Answers per (user, tenant): a tenant-bound grant only under its tenant."""

    scopes: dict[tuple[int, int | None], list[str]]
    calls: list[tuple[int, int | None]] = field(default_factory=list)
    gate: asyncio.Event | None = None

    async def ResolvePlatformPerms(self, request: Any) -> _Response:  # noqa: N802
        key = (request["user_id"], request["tenant_id"])
        self.calls.append(key)
        if self.gate is not None:
            await self.gate.wait()
        return _Response(user_id=key[0], bits=0, scopes=self.scopes.get(key, []))


def test_a_global_read_does_not_answer_a_tenant_read() -> None:
    stub = _TenantStub({(1, None): [], (1, 7): ["colo.scan"]})
    resolver = _resolver(stub)

    async def check() -> None:
        assert not await resolver.has_scope(1, "colo.scan")
        assert await resolver.has_scope(1, "colo.scan", tenant_id=7)
        assert stub.calls == [(1, None), (1, 7)], "the tenant read fetched"
        assert not await resolver.has_scope(1, "colo.scan"), (
            "and the global stays global"
        )

    _run(check())


def test_one_tenant_does_not_answer_for_another() -> None:
    stub = _TenantStub({(1, 7): ["colo.scan"], (1, 8): []})
    resolver = _resolver(stub)

    async def check() -> None:
        assert await resolver.has_scope(1, "colo.scan", tenant_id=7)
        assert not await resolver.has_scope(1, "colo.scan", tenant_id=8)
        assert stub.calls == [(1, 7), (1, 8)]
        assert await resolver.has_scope(1, "colo.scan", tenant_id=7)
        assert len(stub.calls) == 2, "each tenant's answer is cached under its own key"

    _run(check())


def test_invalidating_a_user_evicts_every_tenant_variant() -> None:
    stub = _TenantStub(
        {(1, None): ["a"], (1, 7): ["b"], (1, 8): ["c"], (2, None): ["d"]}
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
            return _Response(user_id=request["user_id"], bits=0, scopes=["x"])

    stub = _Flaky()
    resolver = _resolver(stub)

    async def check() -> None:
        with pytest.raises(RuntimeError):
            await resolver.get(1, tenant_id=7)
        assert resolver._inflight == {}
        assert resolver.metrics.grpc_errors == 1
        assert await resolver.has_scope(1, "x", tenant_id=7)

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

    def __init__(self, scopes: dict[tuple[int, int | None], list[str]]) -> None:
        self.scopes = scopes
        self.calls: list[tuple[int, int | None]] = []
        self.started: list[asyncio.Event] = []
        self.release: list[asyncio.Event] = []

    async def ResolvePlatformPerms(self, request: Any) -> _Response:  # noqa: N802
        key = (request["user_id"], request["tenant_id"])
        answer = list(self.scopes.get(key, []))
        self.calls.append(key)
        started, release = (
            self.started[len(self.calls) - 1],
            self.release[len(self.calls) - 1],
        )
        started.set()
        await release.wait()
        return _Response(user_id=key[0], bits=0, scopes=answer)

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
    provider = _Gated({(1, tenant): ["dcim.manage"]})
    provider.arm(2)
    resolver = _gated_resolver(provider)

    stale = asyncio.create_task(resolver.get(1, tenant_id=tenant))
    await _within(
        provider.started[0].wait(), "the first fetch never reached the provider"
    )

    provider.scopes[(1, tenant)] = []  # the grant is revoked...
    resolver.invalidate(1)  # ...and the user invalidated, mid-fetch
    provider.release[0].set()

    old = await _within(stale, "the original awaiter never got its answer")
    assert "dcim.manage" in old.scopes, "the original awaiter gets its answer"
    assert (1, tenant) not in resolver._cache, "but the cache is not repopulated"

    provider.release[1].set()
    fresh = await _within(
        resolver.get(1, tenant_id=tenant),
        "a read after the invalidation never completed",
    )
    assert "dcim.manage" not in fresh.scopes
    assert provider.calls == [(1, tenant), (1, tenant)]
    assert resolver._inflight == {}
    assert resolver._epochs == {} and resolver._running == {}


def test_an_answer_fetched_before_an_invalidation_is_not_cached() -> None:
    _run(_revoked_while_in_flight(None))


def test_an_answer_fetched_before_an_invalidation_is_not_cached_for_a_tenant() -> None:
    _run(_revoked_while_in_flight(7))


def test_a_reader_after_an_invalidation_starts_a_fresh_fetch() -> None:
    async def check() -> None:
        provider = _Gated({(1, None): ["dcim.manage"]})
        provider.arm(2)
        resolver = _gated_resolver(provider)

        stale = asyncio.create_task(resolver.get(1))
        await _within(
            provider.started[0].wait(), "the first fetch never reached the provider"
        )
        provider.scopes[(1, None)] = []
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
        assert (
            "dcim.manage"
            in (
                await _within(
                    stale, "the stale fetch's own awaiter never got its answer"
                )
            ).scopes
        )
        assert resolver._inflight.get((1, None)) is fresh_task, (
            "the stale fetch's cleanup left the fresh entry alone"
        )
        assert (1, None) not in resolver._cache

        provider.release[1].set()
        assert (
            "dcim.manage"
            not in (await _within(fresh, "the fresh fetch never answered")).scopes
        )
        assert resolver._cache[(1, None)].scopes == frozenset()
        assert resolver._epochs == {} and resolver._running == {}

    _run(check())


def test_readers_within_one_epoch_still_share_a_fetch() -> None:
    async def check() -> None:
        provider = _Gated({(1, 7): ["x"]})
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
    stub = _TenantStub({(1, None): ["x"]})
    resolver = _resolver(stub)

    async def check() -> None:
        await _within(resolver.get(1), "the read never completed")
        for uid in range(100):
            resolver.invalidate(uid)
        resolver.invalidate_many(list(range(100, 200)))
        assert resolver._epochs == {} and resolver._running == {}

    _run(check())
