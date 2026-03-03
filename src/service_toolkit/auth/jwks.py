"""JWKS cache for auth-service validation."""

from __future__ import annotations

import asyncio
import logging
import time
from threading import Lock
from typing import TYPE_CHECKING

try:
    import httpx
except ModuleNotFoundError as exc:  # pragma: no cover - optional dependency
    if exc.name == "httpx":
        raise ModuleNotFoundError(
            "Auth helpers require the optional 'auth' extra. "
            "Install with 'pip install service-toolkit[auth]'."
        ) from exc
    raise

if TYPE_CHECKING:
    from typing import Any

logger = logging.getLogger(__name__)


class JWKSCache:
    """Cache JWKS keys with a TTL to avoid frequent network calls."""

    _shared_caches: dict[tuple[str, int, float], JWKSCache] = {}
    _shared_lock = Lock()

    def __init__(self, *, url: str, ttl_seconds: int, timeout_seconds: float) -> None:
        self._url = url
        self._ttl_seconds = ttl_seconds
        self._timeout_seconds = timeout_seconds
        self._expires_at = 0.0
        self._keys: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    @classmethod
    def shared(
        cls, *, url: str, ttl_seconds: int, timeout_seconds: float
    ) -> JWKSCache:
        """Return a process-wide cache for a specific JWKS endpoint config."""

        key = (url, int(ttl_seconds), float(timeout_seconds))
        with cls._shared_lock:
            cache = cls._shared_caches.get(key)
            if cache is None:
                cache = cls(
                    url=url,
                    ttl_seconds=int(ttl_seconds),
                    timeout_seconds=float(timeout_seconds),
                )
                cls._shared_caches[key] = cache
            return cache

    async def get_key(self, kid: str) -> dict[str, Any] | None:
        keys = await self._get_keys()
        return keys.get(kid)

    async def _get_keys(self) -> dict[str, dict[str, Any]]:
        now = time.monotonic()
        if self._keys and now < self._expires_at:
            return self._keys

        async with self._lock:
            now = time.monotonic()
            if self._keys and now < self._expires_at:
                return self._keys

            try:
                async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                    response = await client.get(self._url)
                    response.raise_for_status()
                    payload = response.json()
            except httpx.HTTPError:
                # Best-effort fallback: use stale keys (or empty set) to avoid
                # turning transient network errors into 500s across services.
                logger.exception("Failed to fetch JWKS keys; using cached value")
                return self._keys

            keys = {
                key.get("kid"): key for key in payload.get("keys", []) if key.get("kid")
            }
            self._keys = keys
            self._expires_at = now + self._ttl_seconds
            return self._keys


__all__ = ["JWKSCache"]
