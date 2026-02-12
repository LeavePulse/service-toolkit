"""Redis helper utilities.

This module intentionally stays small and explicit:
- Configuration loading (`RedisSettings`)
- Client lifecycle (`RedisClient`)
- Key namespacing (`Keyspace`)
- Distributed locks (`RedisLock`)
- Lightweight JSON caching helpers (`RedisCache`)

All Redis functionality is optional and requires the `redis` extra:
`pip install service-toolkit[redis]`.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import random
import secrets
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Generic, TypeVar
from urllib.parse import quote

T = TypeVar("T")

DEFAULT_REDIS_HOST = "127.0.0.1"
DEFAULT_REDIS_PORT = 6379
DEFAULT_REDIS_DB = 0


def _import_redis_async():
    try:  # pragma: no cover - import guard
        import redis.asyncio as redis_async  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:  # pragma: no cover - runtime guard
        if exc.name == "redis":
            raise ModuleNotFoundError(
                "Redis helpers require the optional 'redis' extra. "
                "Install with 'pip install service-toolkit[redis]'."
            ) from exc
        raise
    return redis_async


def _parse_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:  # pragma: no cover - defensive
        raise ValueError(f"Invalid integer value: {value!r}") from exc


def _parse_float(value: str | None, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except ValueError as exc:  # pragma: no cover - defensive
        raise ValueError(f"Invalid float value: {value!r}") from exc


def ttl_with_jitter(
    ttl_seconds: int,
    *,
    ratio: float = 0.1,
    max_jitter_seconds: int | None = None,
) -> int:
    """Return a TTL reduced by random jitter to avoid synchronized expirations."""

    if ttl_seconds <= 0:
        return ttl_seconds
    if ratio <= 0:
        return ttl_seconds
    jitter_limit = max(0.0, ttl_seconds * ratio)
    if max_jitter_seconds is not None:
        jitter_limit = min(jitter_limit, float(max_jitter_seconds))
    jitter = random.uniform(0.0, jitter_limit)  # noqa: S311 - non-crypto jitter
    return max(1, int(ttl_seconds - jitter))


@dataclass(slots=True)
class RedisSettings:
    """Configuration parameters for Redis connectivity."""

    url: str | None = None
    host: str = DEFAULT_REDIS_HOST
    port: int = DEFAULT_REDIS_PORT
    db: int = DEFAULT_REDIS_DB
    username: str | None = None
    password: str | None = None

    socket_connect_timeout: float = 2.0
    socket_timeout: float = 2.0
    health_check_interval: float = 30.0
    max_connections: int | None = None

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        prefix: str = "REDIS_",
        env_file: str | os.PathLike[str] | None = None,
        case_sensitive: bool = False,
    ) -> "RedisSettings":
        """Create settings from environment variables.

        Prefers :mod:`env_settings` when available to keep configuration consistent
        across services, but falls back to simple environment parsing.
        """

        try:
            from env_settings import BaseSettings as _BaseSettings  # type: ignore
        except ModuleNotFoundError:  # pragma: no cover - optional dependency
            _BaseSettings = None

        if _BaseSettings is not None:

            class _RedisConfig(_BaseSettings):  # type: ignore[misc]
                url: str | None = None
                host: str = DEFAULT_REDIS_HOST
                port: int = DEFAULT_REDIS_PORT
                db: int = DEFAULT_REDIS_DB
                username: str | None = None
                password: str | None = None
                socket_connect_timeout: float = 2.0
                socket_timeout: float = 2.0
                health_check_interval: float = 30.0
                max_connections: int | None = None

            loaded = _RedisConfig.load(
                env=env,
                env_file=env_file,
                prefix=prefix,
                case_sensitive=case_sensitive,
            )
            return cls(
                url=loaded.url,
                host=loaded.host,
                port=loaded.port,
                db=loaded.db,
                username=loaded.username,
                password=loaded.password,
                socket_connect_timeout=loaded.socket_connect_timeout,
                socket_timeout=loaded.socket_timeout,
                health_check_interval=loaded.health_check_interval,
                max_connections=loaded.max_connections,
            )

        source = env if env is not None else os.environ
        return cls(
            url=source.get(f"{prefix}URL"),
            host=source.get(f"{prefix}HOST", DEFAULT_REDIS_HOST),
            port=_parse_int(source.get(f"{prefix}PORT"), DEFAULT_REDIS_PORT),
            db=_parse_int(source.get(f"{prefix}DB"), DEFAULT_REDIS_DB),
            username=source.get(f"{prefix}USERNAME"),
            password=source.get(f"{prefix}PASSWORD"),
            socket_connect_timeout=_parse_float(
                source.get(f"{prefix}SOCKET_CONNECT_TIMEOUT"), cls.socket_connect_timeout
            ),
            socket_timeout=_parse_float(
                source.get(f"{prefix}SOCKET_TIMEOUT"), cls.socket_timeout
            ),
            health_check_interval=_parse_float(
                source.get(f"{prefix}HEALTH_CHECK_INTERVAL"), cls.health_check_interval
            ),
            max_connections=(
                _parse_int(source.get(f"{prefix}MAX_CONNECTIONS"), 0) or None
                if source.get(f"{prefix}MAX_CONNECTIONS") is not None
                else None
            ),
        )

    @property
    def redis_url(self) -> str:
        if self.url:
            return self.url

        auth = ""
        if self.username and self.password:
            auth = f"{quote(self.username)}:{quote(self.password)}@"
        elif self.password:
            auth = f":{quote(self.password)}@"

        return f"redis://{auth}{self.host}:{self.port}/{self.db}"


class RedisClient:
    """Async Redis client wrapper with explicit lifecycle."""

    def __init__(self, settings: RedisSettings):
        self._settings = settings
        self._client: Any | None = None

    @property
    def settings(self) -> RedisSettings:
        return self._settings

    @property
    def client(self) -> Any:
        if self._client is None:
            raise RuntimeError("Redis client not connected yet")
        return self._client

    async def connect(self) -> Any:
        if self._client is not None:
            return self._client

        redis_async = _import_redis_async()

        kwargs: dict[str, Any] = {
            "decode_responses": False,
            "socket_connect_timeout": self._settings.socket_connect_timeout,
            "socket_timeout": self._settings.socket_timeout,
            "retry_on_timeout": True,
            "health_check_interval": self._settings.health_check_interval,
        }
        if self._settings.max_connections is not None:
            kwargs["max_connections"] = self._settings.max_connections

        self._client = redis_async.Redis.from_url(self._settings.redis_url, **kwargs)
        await self._client.ping()
        return self._client

    async def aclose(self) -> None:
        if self._client is None:
            return
        client = self._client
        self._client = None

        close_fn = getattr(client, "aclose", None) or getattr(client, "close", None)
        if close_fn is None:
            return
        result = close_fn()
        if inspect.isawaitable(result):
            await result

    async def __aenter__(self) -> "RedisClient":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()


@dataclass(frozen=True, slots=True)
class Keyspace:
    """Build Redis keys with a stable namespace."""

    prefix: str
    separator: str = ":"

    def key(self, *parts: str) -> str:
        cleaned: list[str] = []
        for part in (self.prefix, *parts):
            part = str(part).strip()
            if not part:
                continue
            cleaned.append(part.strip(self.separator))
        return self.separator.join(cleaned)


_LOCK_RELEASE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
  return redis.call("del", KEYS[1])
else
  return 0
end
""".strip()

_LOCK_EXTEND_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
  return redis.call("pexpire", KEYS[1], ARGV[2])
else
  return 0
end
""".strip()


class RedisLock:
    """Simple distributed lock using SET NX PX and a compare-and-delete release."""

    def __init__(
        self,
        client: Any,
        key: str,
        *,
        ttl_seconds: float,
    ):
        self._client = client
        self._key = key
        self._ttl_seconds = ttl_seconds
        self._token = secrets.token_hex(16)
        self._held = False

    @property
    def key(self) -> str:
        return self._key

    @property
    def held(self) -> bool:
        return self._held

    async def acquire(self) -> bool:
        ttl_ms = max(1, int(self._ttl_seconds * 1000))
        result = await self._client.set(self._key, self._token, nx=True, px=ttl_ms)
        self._held = bool(result)
        return self._held

    async def release(self) -> bool:
        if not self._held:
            return False
        result = await self._client.eval(_LOCK_RELEASE_SCRIPT, 1, self._key, self._token)
        self._held = False
        return int(result or 0) == 1

    async def extend(self, *, ttl_seconds: float | None = None) -> bool:
        """Extend the lock TTL if still held by this instance."""

        if not self._held:
            return False
        ttl = self._ttl_seconds if ttl_seconds is None else float(ttl_seconds)
        ttl_ms = max(1, int(ttl * 1000))
        result = await self._client.eval(
            _LOCK_EXTEND_SCRIPT,
            1,
            self._key,
            self._token,
            str(ttl_ms),
        )
        ok = int(result or 0) == 1
        if not ok:
            self._held = False
        return ok

    async def acquire_with_wait(
        self,
        *,
        timeout_seconds: float = 5.0,
        initial_delay_seconds: float = 0.05,
        max_delay_seconds: float = 0.5,
    ) -> bool:
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        delay = max(0.0, initial_delay_seconds)
        while True:
            if await self.acquire():
                return True
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(delay)
            delay = min(max_delay_seconds, delay * 1.5)

    async def __aenter__(self) -> "RedisLock":
        if not await self.acquire():
            raise RuntimeError(f"Failed to acquire Redis lock: {self._key}")
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.release()


class LeaderLease:
    """Maintain a renewable leader lease for background tasks.

    This is a pragmatic alternative to running background consumers in a
    dedicated sidecar process: any worker can attempt to become leader, but only
    the leader should execute the protected task(s).
    """

    def __init__(
        self,
        client: Any,
        key: str,
        *,
        ttl_seconds: float = 30.0,
        renew_interval_seconds: float | None = None,
        on_lost: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._client = client
        self._lock = RedisLock(client, key, ttl_seconds=ttl_seconds)
        self._ttl_seconds = float(ttl_seconds)
        self._renew_interval = (
            float(renew_interval_seconds)
            if renew_interval_seconds is not None
            else max(1.0, self._ttl_seconds / 3.0)
        )
        self._on_lost = on_lost
        self._task: asyncio.Task[None] | None = None
        self._lost = asyncio.Event()

    @property
    def key(self) -> str:
        return self._lock.key

    @property
    def held(self) -> bool:
        return self._lock.held and not self._lost.is_set()

    async def acquire(self) -> bool:
        if self._task is not None and not self._task.done():
            return self.held

        ok = await self._lock.acquire()
        if not ok:
            return False

        self._lost.clear()
        self._task = asyncio.create_task(self._renew_loop())
        return True

    async def release(self) -> None:
        task = self._task
        self._task = None
        self._lost.set()

        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:  # pragma: no cover - expected
                pass

        await self._lock.release()

    async def wait_lost(self) -> None:
        task = self._task
        if task is None:
            return
        try:
            await task
        except asyncio.CancelledError:  # pragma: no cover - normal shutdown
            return

    async def _renew_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._renew_interval)
                ok = await self._lock.extend(ttl_seconds=self._ttl_seconds)
                if ok:
                    continue
                self._lost.set()
                if self._on_lost is not None:
                    asyncio.create_task(self._safe_on_lost())
                return
        except asyncio.CancelledError:  # pragma: no cover
            return

    async def _safe_on_lost(self) -> None:
        try:
            await self._on_lost()  # type: ignore[misc]
        except Exception:  # noqa: BLE001  # pragma: no cover
            return


class RedisCache(Generic[T]):
    """Lightweight JSON cache wrapper."""

    def __init__(
        self,
        client: Any,
        *,
        keyspace: Keyspace,
        default_ttl_seconds: int = 300,
        ttl_jitter_ratio: float = 0.0,
        ttl_jitter_max_seconds: int | None = None,
    ):
        self._client = client
        self._keyspace = keyspace
        self._default_ttl_seconds = default_ttl_seconds
        self._ttl_jitter_ratio = ttl_jitter_ratio
        self._ttl_jitter_max_seconds = ttl_jitter_max_seconds

        try:  # pragma: no cover - optional
            import msgspec.json as _msgspec_json  # type: ignore[import-not-found]
        except ModuleNotFoundError:
            _msgspec_json = None
        self._msgspec_json = _msgspec_json

    def _full_key(self, key: str) -> str:
        return self._keyspace.key(key)

    def _encode_json(self, value: Any) -> bytes:
        if self._msgspec_json is not None:
            return self._msgspec_json.encode(value)
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    def _decode_json(self, data: bytes, *, type: Any | None = None) -> Any:
        if self._msgspec_json is not None:
            if type is None:
                return self._msgspec_json.decode(data)
            return self._msgspec_json.decode(data, type=type)
        decoded = json.loads(data.decode("utf-8"))
        return decoded

    def _effective_ttl(self, ttl_seconds: int | None) -> int:
        ttl = self._default_ttl_seconds if ttl_seconds is None else int(ttl_seconds)
        if self._ttl_jitter_ratio > 0:
            ttl = ttl_with_jitter(
                ttl,
                ratio=self._ttl_jitter_ratio,
                max_jitter_seconds=self._ttl_jitter_max_seconds,
            )
        return ttl

    async def get_bytes(self, key: str) -> bytes | None:
        return await self._client.get(self._full_key(key))

    async def set_bytes(self, key: str, value: bytes, *, ttl_seconds: int | None = None) -> bool:
        ttl = self._effective_ttl(ttl_seconds)
        result = await self._client.set(self._full_key(key), value, ex=ttl)
        return bool(result)

    async def delete(self, key: str) -> int:
        return int(await self._client.delete(self._full_key(key)))

    async def get_json(self, key: str, *, type: Any | None = None) -> Any | None:
        data = await self.get_bytes(key)
        if data is None:
            return None
        return self._decode_json(data, type=type)

    async def set_json(self, key: str, value: Any, *, ttl_seconds: int | None = None) -> bool:
        return await self.set_bytes(key, self._encode_json(value), ttl_seconds=ttl_seconds)

    async def get_or_set_json(
        self,
        key: str,
        producer: Callable[[], Awaitable[Any]],
        *,
        ttl_seconds: int | None = None,
        lock_ttl_seconds: float = 10.0,
        wait_timeout_seconds: float = 2.0,
    ) -> Any:
        cached = await self.get_json(key)
        if cached is not None:
            return cached

        lock_key = self._keyspace.key(f"{key}:lock")
        lock = RedisLock(self._client, lock_key, ttl_seconds=lock_ttl_seconds)
        if await lock.acquire():
            try:
                cached = await self.get_json(key)
                if cached is not None:
                    return cached
                value = await producer()
                await self.set_json(key, value, ttl_seconds=ttl_seconds)
                return value
            finally:
                await lock.release()

        # Another worker is computing. Wait briefly for the value to appear.
        deadline = time.monotonic() + max(0.0, wait_timeout_seconds)
        delay = 0.05
        while time.monotonic() < deadline:
            await asyncio.sleep(delay)
            cached = await self.get_json(key)
            if cached is not None:
                return cached
            delay = min(0.5, delay * 1.5)

        # Fallback: compute without lock to avoid indefinite waiting.
        return await producer()


__all__ = [
    "DEFAULT_REDIS_DB",
    "DEFAULT_REDIS_HOST",
    "DEFAULT_REDIS_PORT",
    "Keyspace",
    "LeaderLease",
    "RedisCache",
    "RedisClient",
    "RedisLock",
    "RedisSettings",
    "ttl_with_jitter",
]
