"""Provider-agnostic live resolver for per-user platform permission bits.

The resolver keeps an in-process L1 cache of (bits, version) per user and
refreshes entries via a gRPC stub provided by the caller. NATS invalidation
events drop stale entries before TTL expiry.

The stub protocol is structural: any object exposing an async
``ResolvePlatformPerms(request)`` callable returning a response with
``bits`` / ``version`` / ``roles`` / ``max_role_weight`` integer-compatible
attributes is accepted, plus an optional ``scopes`` list of granted permission
codes. This keeps the toolkit free of any provider-specific imports (e.g.
``auth_service_grpc``).

Two questions, two answers:

* :meth:`PlatformPermsResolver.has_code` answers for a code of the PLATFORM
  catalog, the one bit space the provider packs ``bits`` from. A bit index from
  any other catalog names a different permission in that space, so the resolver
  refuses a mapping other than the one it was built with.
* :meth:`PlatformPermsResolver.has_scope` answers for any code by its exact
  string, from the scopes the provider resolved. It is the gate for every code
  outside the platform catalog.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from service_toolkit.messaging.nats import NATSClient

logger = logging.getLogger(__name__)


class _PermsRequestProto(Protocol):
    user_id: int
    tenant_id: int | None


class _PermsResponseProto(Protocol):
    user_id: int
    bits: int
    version: int
    roles: list[str]
    max_role_weight: int
    scopes: list[str]


class PermsStubProto(Protocol):
    """Structural protocol for the gRPC stub used by the resolver."""

    async def ResolvePlatformPerms(  # noqa: N802 — gRPC method name
        self, request: Any
    ) -> _PermsResponseProto: ...


class RequestFactory(Protocol):
    """Builds a stub request object from user_id/tenant_id."""

    def __call__(self, *, user_id: int, tenant_id: int | None) -> Any: ...


@dataclass(slots=True)
class _CacheEntry:
    bits: int
    version: int
    roles: tuple[str, ...]
    max_role_weight: int
    scopes: frozenset[str]
    fetched_at: float


#: A resolved subject: the user, and the tenant the answer was resolved for.
#: ``None`` is the global (tenant-less) resolution, a key of its own: a
#: tenant-bound grant must never answer a global question, nor the reverse,
#: nor one tenant's another's.
_SubjectKey = tuple[int, int | None]


@dataclass(slots=True)
class ResolverMetrics:
    cache_hits: int = 0
    cache_misses: int = 0
    invalidations_received: int = 0
    grpc_errors: int = 0
    last_fetch_seconds: float = 0.0


@dataclass(slots=True)
class PlatformPerms:
    """Snapshot returned to callers."""

    user_id: int
    bits: int
    version: int
    roles: tuple[str, ...] = field(default_factory=tuple)
    max_role_weight: int = 0
    scopes: frozenset[str] = field(default_factory=frozenset)


class PlatformPermsResolver:
    """L1-cached, NATS-invalidated live resolver for platform permission bits."""

    def __init__(
        self,
        *,
        stub: PermsStubProto,
        request_factory: RequestFactory,
        ttl_seconds: float = 30.0,
        max_entries: int = 10_000,
        invalidation_subject_prefix: str = "auth.rbac.user.invalidated",
        role_invalidation_subject_prefix: str = "auth.rbac.role.invalidated",
        metrics: ResolverMetrics | None = None,
        platform_bit_mapping: dict[str, int] | None = None,
    ) -> None:
        self._stub = stub
        # The catalog the provider packs ``bits`` from. When given, has_code
        # refuses any other mapping instead of reading its indices against
        # this space (dcim.view at bit 0 meant platform.audit.view).
        self._platform_bit_mapping = platform_bit_mapping
        self._request_factory = request_factory
        self._ttl = float(ttl_seconds)
        self._max_entries = int(max_entries)
        self._user_subject_prefix = invalidation_subject_prefix.rstrip(".")
        self._role_subject_prefix = role_invalidation_subject_prefix.rstrip(".")
        self._cache: dict[_SubjectKey, _CacheEntry] = {}
        self._inflight: dict[_SubjectKey, asyncio.Task[_CacheEntry]] = {}
        # Per-user invalidation epoch, fencing fetches that were already in
        # flight when the user was invalidated: such a fetch still answers its
        # own awaiters but never repopulates the cache. An epoch only matters
        # while a fetch for that user is running, so both maps hold users with
        # a running fetch and nothing else: they are bounded by concurrency,
        # not by how many users were ever seen.
        self._epochs: dict[int, int] = {}
        self._running: dict[int, int] = {}
        self._lock = asyncio.Lock()  # noqa: archlint=cache-lock — subject-invalidated resolver cache, not a generic LookupCache
        self.metrics = metrics or ResolverMetrics()

    @property
    def user_subject_pattern(self) -> str:
        return f"{self._user_subject_prefix}.>"

    @property
    def role_subject_pattern(self) -> str:
        return f"{self._role_subject_prefix}.>"

    async def get(self, user_id: int, *, tenant_id: int | None = None) -> PlatformPerms:
        uid = int(user_id)
        key: _SubjectKey = (uid, None if tenant_id is None else int(tenant_id))
        now = time.monotonic()
        entry = self._cache.get(key)
        if entry is not None and (now - entry.fetched_at) < self._ttl:
            self.metrics.cache_hits += 1
            return PlatformPerms(
                user_id=uid,
                bits=entry.bits,
                version=entry.version,
                roles=entry.roles,
                max_role_weight=entry.max_role_weight,
                scopes=entry.scopes,
            )

        self.metrics.cache_misses += 1
        fresh = await self._fetch_and_cache(key)
        return PlatformPerms(
            user_id=uid,
            bits=fresh.bits,
            version=fresh.version,
            roles=fresh.roles,
            max_role_weight=fresh.max_role_weight,
            scopes=fresh.scopes,
        )

    async def bits(self, user_id: int, *, tenant_id: int | None = None) -> int:
        snapshot = await self.get(int(user_id), tenant_id=tenant_id)
        return snapshot.bits

    async def has_code(
        self,
        user_id: int,
        code: str,
        bit_mapping: dict[str, int],
        *,
        tenant_id: int | None = None,
    ) -> bool:
        """Whether the user holds *code* of the PLATFORM catalog.

        Only the catalog the provider packs ``bits`` from is meaningful here.
        Passing another catalog raises: its indices would be read against the
        platform space and grant whatever platform code shares the index. Use
        :meth:`has_scope` for a code outside the platform catalog.
        """
        if (
            self._platform_bit_mapping is not None
            and bit_mapping is not self._platform_bit_mapping
            and bit_mapping != self._platform_bit_mapping
        ):
            msg = (
                f"has_code({code!r}) was given a catalog other than the platform "
                "one the bits are packed from; use has_scope for this code"
            )
            raise ValueError(msg)
        bit = bit_mapping.get(code)
        if bit is None:
            return False
        bits = await self.bits(int(user_id), tenant_id=tenant_id)
        return bool(bits & (1 << int(bit)))

    async def has_scope(
        self,
        user_id: int,
        code: str,
        *,
        tenant_id: int | None = None,
    ) -> bool:
        """Whether the user holds *code*, by its exact string.

        Answered from the scopes the provider resolved, cached and invalidated
        with the bits. A code nobody granted, a code the provider does not know,
        and a provider too old to send scopes all deny.
        """
        snapshot = await self.get(int(user_id), tenant_id=tenant_id)
        return code in snapshot.scopes

    def invalidate(self, user_id: int) -> None:
        """Drop every cached answer for a user: global and every tenant."""
        self.invalidate_many([int(user_id)])

    def invalidate_many(self, user_ids: list[int]) -> None:
        """Drop every answer for these users and fence their fetches in flight.

        Each user's epoch moves on, so a fetch that started before this call
        stores nothing; its in-flight entries leave the coalescing map, so a
        reader after this call starts (or joins) a fresh fetch instead of
        awaiting the stale one.
        """
        doomed = {int(uid) for uid in user_ids}
        for uid in doomed:
            if uid in self._running:
                self._epochs[uid] = self._epochs.get(uid, 0) + 1
        for key in [k for k in self._cache if k[0] in doomed]:
            self._cache.pop(key, None)
        for key in [k for k in self._inflight if k[0] in doomed]:
            self._inflight.pop(key, None)

    def clear(self) -> None:
        self.invalidate_many(list({k[0] for k in self._cache} | set(self._running)))
        self._cache.clear()

    async def _fetch_and_cache(self, key: _SubjectKey) -> _CacheEntry:
        existing = self._inflight.get(key)
        if existing is not None:
            return await existing

        user_id, tenant_id = key
        async with self._lock:
            existing = self._inflight.get(key)
            if existing is None:
                epoch = self._epochs.get(user_id, 0)
                task = asyncio.create_task(self._fetch(user_id, tenant_id))
                self._inflight[key] = task
                self._running[user_id] = self._running.get(user_id, 0) + 1
        if existing is not None:
            return await existing

        try:
            entry = await task
            # Invalidated while in flight: the answer predates the change, so
            # it serves the readers already waiting on it and is not cached.
            if self._epochs.get(user_id, 0) == epoch:
                self._store(key, entry)
            return entry
        finally:
            # On success and on failure alike: a failed fetch is retried by the
            # next caller rather than awaited forever. Only this fetch's own
            # entry is removed; an invalidation may have put a fresh one there.
            if self._inflight.get(key) is task:
                self._inflight.pop(key, None)
            remaining = self._running[user_id] - 1
            if remaining:
                self._running[user_id] = remaining
            else:
                del self._running[user_id]
                self._epochs.pop(user_id, None)

    async def _fetch(self, user_id: int, tenant_id: int | None) -> _CacheEntry:
        started = time.perf_counter()
        try:
            request = self._request_factory(user_id=user_id, tenant_id=tenant_id)
            response = await self._stub.ResolvePlatformPerms(request)
        except Exception:
            self.metrics.grpc_errors += 1
            raise
        finally:
            self.metrics.last_fetch_seconds = time.perf_counter() - started

        return _CacheEntry(
            bits=int(getattr(response, "bits", 0) or 0),
            version=int(getattr(response, "version", 0) or 0),
            roles=tuple(str(r) for r in getattr(response, "roles", []) or []),
            max_role_weight=int(getattr(response, "max_role_weight", 0) or 0),
            scopes=frozenset(str(s) for s in getattr(response, "scopes", []) or []),
            fetched_at=time.monotonic(),
        )

    def _store(self, key: _SubjectKey, entry: _CacheEntry) -> None:
        # max_entries counts subjects, (user, tenant) pairs, not users.
        if key not in self._cache and len(self._cache) >= self._max_entries:
            # Drop the oldest entry (cheap LRU-ish eviction).
            oldest = min(self._cache, key=lambda k: self._cache[k].fetched_at)
            self._cache.pop(oldest, None)
        self._cache[key] = entry

    async def subscribe_invalidations(self, nats_client: NATSClient) -> None:
        """Subscribe to NATS invalidation subjects via the provided client."""
        await nats_client.subscribe(
            self.user_subject_pattern,
            callback=self._handle_user_invalidation,
        )
        await nats_client.subscribe(
            self.role_subject_pattern,
            callback=self._handle_role_invalidation,
        )

    async def _handle_user_invalidation(self, msg: Any) -> None:
        self.metrics.invalidations_received += 1
        try:
            user_id = self._user_id_from_subject(str(msg.subject))
            if user_id is not None:
                self.invalidate(user_id)
                return
            payload = json.loads(bytes(msg.data).decode("utf-8") or "{}")
            data = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(data, dict) and "user_id" in data:
                self.invalidate(int(data["user_id"]))
        except ValueError, KeyError, TypeError, json.JSONDecodeError:
            logger.exception("Failed to handle RBAC user invalidation message")

    async def _handle_role_invalidation(self, msg: Any) -> None:
        self.metrics.invalidations_received += 1
        try:
            payload = json.loads(bytes(msg.data).decode("utf-8") or "{}")
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, dict):
                return
            user_ids = data.get("user_ids") or []
            if isinstance(user_ids, list):
                self.invalidate_many([int(uid) for uid in user_ids])
        except ValueError, KeyError, TypeError, json.JSONDecodeError:
            logger.exception("Failed to handle RBAC role invalidation message")

    def _user_id_from_subject(self, subject: str) -> int | None:
        prefix = f"{self._user_subject_prefix}."
        if not subject.startswith(prefix):
            return None
        tail = subject[len(prefix) :]
        try:
            return int(tail)
        except TypeError, ValueError:
            return None


__all__ = [
    "PermsStubProto",
    "PlatformPerms",
    "PlatformPermsResolver",
    "RequestFactory",
    "ResolverMetrics",
]
