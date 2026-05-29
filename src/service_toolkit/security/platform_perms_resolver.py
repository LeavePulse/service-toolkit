"""Provider-agnostic live resolver for per-user platform permission bits.

The resolver keeps an in-process L1 cache of (bits, version) per user and
refreshes entries via a gRPC stub provided by the caller. NATS invalidation
events drop stale entries before TTL expiry.

The stub protocol is structural: any object exposing an async
``ResolvePlatformPerms(request)`` callable returning a response with
``bits`` / ``version`` / ``roles`` / ``max_role_weight`` integer-compatible
attributes is accepted. This keeps the toolkit free of any provider-specific
imports (e.g. ``auth_service_grpc``).
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
    fetched_at: float


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
    ) -> None:
        self._stub = stub
        self._request_factory = request_factory
        self._ttl = float(ttl_seconds)
        self._max_entries = int(max_entries)
        self._user_subject_prefix = invalidation_subject_prefix.rstrip(".")
        self._role_subject_prefix = role_invalidation_subject_prefix.rstrip(".")
        self._cache: dict[int, _CacheEntry] = {}
        self._inflight: dict[int, asyncio.Task[_CacheEntry]] = {}
        self._lock = asyncio.Lock()  # noqa: archlint=cache-lock — subject-invalidated resolver cache, not a generic LookupCache
        self.metrics = metrics or ResolverMetrics()

    @property
    def user_subject_pattern(self) -> str:
        return f"{self._user_subject_prefix}.>"

    @property
    def role_subject_pattern(self) -> str:
        return f"{self._role_subject_prefix}.>"

    async def get(
        self, user_id: int, *, tenant_id: int | None = None
    ) -> PlatformPerms:
        uid = int(user_id)
        now = time.monotonic()
        entry = self._cache.get(uid)
        if entry is not None and (now - entry.fetched_at) < self._ttl:
            self.metrics.cache_hits += 1
            return PlatformPerms(
                user_id=uid,
                bits=entry.bits,
                version=entry.version,
                roles=entry.roles,
                max_role_weight=entry.max_role_weight,
            )

        self.metrics.cache_misses += 1
        fresh = await self._fetch_and_cache(uid, tenant_id)
        return PlatformPerms(
            user_id=uid,
            bits=fresh.bits,
            version=fresh.version,
            roles=fresh.roles,
            max_role_weight=fresh.max_role_weight,
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
        bit = bit_mapping.get(code)
        if bit is None:
            return False
        bits = await self.bits(int(user_id), tenant_id=tenant_id)
        return bool(bits & (1 << int(bit)))

    def invalidate(self, user_id: int) -> None:
        """Drop a single user's cached entry."""
        self._cache.pop(int(user_id), None)

    def invalidate_many(self, user_ids: list[int]) -> None:
        for uid in user_ids:
            self._cache.pop(int(uid), None)

    def clear(self) -> None:
        self._cache.clear()

    async def _fetch_and_cache(
        self, user_id: int, tenant_id: int | None
    ) -> _CacheEntry:
        existing = self._inflight.get(user_id)
        if existing is not None:
            return await existing

        async with self._lock:
            existing = self._inflight.get(user_id)
            if existing is not None:
                return await existing

            task = asyncio.create_task(self._fetch(user_id, tenant_id))
            self._inflight[user_id] = task

        try:
            entry = await task
            self._store(user_id, entry)
            return entry
        finally:
            self._inflight.pop(user_id, None)

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
            fetched_at=time.monotonic(),
        )

    def _store(self, user_id: int, entry: _CacheEntry) -> None:
        if len(self._cache) >= self._max_entries:
            # Drop the oldest entry (cheap LRU-ish eviction).
            oldest_uid = min(self._cache, key=lambda uid: self._cache[uid].fetched_at)
            self._cache.pop(oldest_uid, None)
        self._cache[int(user_id)] = entry

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
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
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
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            logger.exception("Failed to handle RBAC role invalidation message")

    def _user_id_from_subject(self, subject: str) -> int | None:
        prefix = f"{self._user_subject_prefix}."
        if not subject.startswith(prefix):
            return None
        tail = subject[len(prefix) :]
        try:
            return int(tail)
        except (TypeError, ValueError):
            return None


__all__ = [
    "PermsStubProto",
    "PlatformPerms",
    "PlatformPermsResolver",
    "RequestFactory",
    "ResolverMetrics",
]
