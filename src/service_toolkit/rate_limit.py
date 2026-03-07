"""Reusable request rate limiting helpers."""

from __future__ import annotations

import inspect
import re
from asyncio import Lock
from collections import OrderedDict
from enum import StrEnum
from functools import wraps
from hashlib import blake2b
from time import monotonic
from typing import TYPE_CHECKING, ParamSpec, TypeVar, cast

from litestar import status_codes
from litestar.exceptions import HTTPException

from .request_ip import resolve_client_ip

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from litestar import Request


P = ParamSpec("P")
R = TypeVar("R")

_BUCKET_PART_RE = re.compile(r"[^a-z0-9:_-]+")
_SUBJECT_PART_RE = re.compile(r"[^a-z0-9:_-]+")
_FALLBACK_SUBJECT = "unknown"
_REDIS_FIXED_WINDOW_SCRIPT = """
local current = redis.call("INCR", KEYS[1])
if current == 1 then
  redis.call("EXPIRE", KEYS[1], ARGV[1])
end
return current
""".strip()


class RateLimitFailureMode(StrEnum):
    LOCAL_FALLBACK = "local_fallback"
    BYPASS = "bypass"
    RAISE = "raise"


class _LocalWindowLimiter:
    def __init__(self, *, max_entries: int = 65_536) -> None:
        self._entries: OrderedDict[str, tuple[int, float]] = OrderedDict()
        self._lock = Lock()
        self._max_entries = max(1, int(max_entries))

    async def enforce(
        self,
        *,
        key: str,
        limit: int,
        window_seconds: int,
        error_detail: str,
    ) -> None:
        now = monotonic()
        ttl = max(1, int(window_seconds))
        expires_at = now + ttl

        async with self._lock:
            for stale_key, (_, stale_expires_at) in list(self._entries.items()):
                if stale_expires_at <= now:
                    self._entries.pop(stale_key, None)

            current_count = 0
            current_expires_at = expires_at
            current = self._entries.get(key)
            if current is not None:
                current_count, current_expires_at = current
                if current_expires_at <= now:
                    current_count = 0
                    current_expires_at = expires_at

            if current_count >= limit:
                raise HTTPException(
                    status_code=status_codes.HTTP_429_TOO_MANY_REQUESTS,
                    detail=error_detail,
                )

            self._entries[key] = (current_count + 1, current_expires_at)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)


_LOCAL_WINDOW_LIMITER = _LocalWindowLimiter()


def _normalize_bucket(bucket: str) -> str:
    normalized = _BUCKET_PART_RE.sub(":", bucket.strip().lower())
    normalized = re.sub(r":{2,}", ":", normalized).strip(":")
    return normalized or _FALLBACK_SUBJECT


def _normalize_subject(subject: str) -> str:
    normalized = _SUBJECT_PART_RE.sub(":", subject.strip().lower())
    normalized = re.sub(r":{2,}", ":", normalized).strip(":")
    return normalized or _FALLBACK_SUBJECT


def _hash_subject(subject: str, *, secret: str | bytes | None = None) -> str:
    secret_bytes: bytes
    if isinstance(secret, str):
        secret_bytes = secret.encode("utf-8")
    elif isinstance(secret, bytes):
        secret_bytes = secret
    else:
        secret_bytes = b""

    digest = blake2b(key=secret_bytes, digest_size=16)
    digest.update(subject.encode("utf-8"))
    return digest.hexdigest()


def _default_subject_resolver(request: Request) -> str:
    if client_ip := resolve_client_ip(request):
        return client_ip
    return _FALLBACK_SUBJECT


def _resolve_redis_backend(
    store: object,
    *,
    key: str,
) -> tuple[object, str] | None:
    redis_client = getattr(store, "_redis", None)
    make_key = getattr(store, "_make_key", None)
    if redis_client is None or not callable(make_key):
        return None

    try:
        redis_key = str(make_key(key))
    except Exception:
        return None
    return redis_client, redis_key


async def _enforce_redis_rate_limit(
    *,
    redis_client: object,
    redis_key: str,
    limit: int,
    window_seconds: int,
    error_detail: str,
) -> None:
    raw_value = await getattr(redis_client, "eval")(
        _REDIS_FIXED_WINDOW_SCRIPT,
        1,
        redis_key,
        max(1, int(window_seconds)),
    )
    current = int(raw_value)
    if current > limit:
        raise HTTPException(
            status_code=status_codes.HTTP_429_TOO_MANY_REQUESTS,
            detail=error_detail,
        )


async def _handle_backend_failure(
    *,
    failure_mode: RateLimitFailureMode,
    key: str,
    limit: int,
    window_seconds: int,
    error_detail: str,
    cause: Exception | None = None,
) -> None:
    if failure_mode is RateLimitFailureMode.BYPASS:
        return
    if failure_mode is RateLimitFailureMode.RAISE:
        if cause is not None:
            raise cause
        raise RuntimeError("Request rate limiter backend is unavailable")
    await _LOCAL_WINDOW_LIMITER.enforce(
        key=key,
        limit=limit,
        window_seconds=window_seconds,
        error_detail=error_detail,
    )


async def enforce_request_rate_limit(
    request: Request,
    *,
    bucket: str,
    limit: int,
    window_seconds: int = 60,
    subject: str | None = None,
    subject_resolver: Callable[[Request], str] | None = None,
    hash_subject: bool = True,
    hash_secret: str | bytes | None = None,
    store_name: str = "main",
    key_prefix: str = "rl",
    error_detail: str = "Too many requests. Please retry later.",
    failure_mode: RateLimitFailureMode = RateLimitFailureMode.LOCAL_FALLBACK,
) -> None:
    """Limit repeated requests for a bucket/subject pair.

    When the configured Litestar store is backed by Redis, the limiter uses an
    atomic Redis fixed-window counter. If Redis is unavailable, the helper
    follows ``failure_mode`` and either falls back to a process-local limiter,
    bypasses limiting, or raises the backend error.
    """
    if limit <= 0:
        return

    resolved_subject = (
        subject
        if subject is not None
        else (
            subject_resolver(request)
            if subject_resolver is not None
            else _default_subject_resolver(request)
        )
    )
    subject_key = _normalize_subject(resolved_subject)
    if hash_subject:
        subject_key = _hash_subject(subject_key, secret=hash_secret)

    key = f"{key_prefix}:{_normalize_bucket(bucket)}:{subject_key}"
    mode = RateLimitFailureMode(failure_mode)

    store = request.app.stores.get(store_name)
    if store is None:
        await _handle_backend_failure(
            failure_mode=mode,
            key=key,
            limit=limit,
            window_seconds=window_seconds,
            error_detail=error_detail,
        )
        return

    backend = _resolve_redis_backend(store, key=key)
    if backend is None:
        await _handle_backend_failure(
            failure_mode=mode,
            key=key,
            limit=limit,
            window_seconds=window_seconds,
            error_detail=error_detail,
            cause=RuntimeError(
                f"Store '{store_name}' does not expose a Redis backend for "
                "distributed rate limiting"
            ),
        )
        return

    redis_client, redis_key = backend
    try:
        await _enforce_redis_rate_limit(
            redis_client=redis_client,
            redis_key=redis_key,
            limit=limit,
            window_seconds=window_seconds,
            error_detail=error_detail,
        )
    except HTTPException:
        raise
    except Exception as exc:
        await _handle_backend_failure(
            failure_mode=mode,
            key=key,
            limit=limit,
            window_seconds=window_seconds,
            error_detail=error_detail,
            cause=exc,
        )


def rate_limited_request(
    *,
    bucket: str,
    limit: int,
    window_seconds: int = 60,
    subject: str | None = None,
    subject_resolver: Callable[[Request], str] | None = None,
    hash_subject: bool = True,
    hash_secret: str | bytes | None = None,
    store_name: str = "main",
    key_prefix: str = "rl",
    error_detail: str = "Too many requests. Please retry later.",
    request_param: str = "request",
    failure_mode: RateLimitFailureMode = RateLimitFailureMode.LOCAL_FALLBACK,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Apply request rate limiting as a decorator.

    The wrapped endpoint must expose a parameter with name ``request_param``.
    """

    def decorator(
        handler: Callable[P, Awaitable[R]],
    ) -> Callable[P, Awaitable[R]]:
        signature = inspect.signature(handler)
        request_index = None
        for index, name in enumerate(signature.parameters):
            if name == request_param:
                request_index = index
                break

        if request_index is None:
            msg = (
                f"rate_limited_request expected '{request_param}' parameter in "
                f"{handler.__qualname__}"
            )
            raise ValueError(msg)

        @wraps(handler)
        async def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            request_obj = kwargs.get(request_param)
            if request_obj is None and request_index < len(args):
                request_obj = args[request_index]
            if request_obj is None:
                msg = (
                    f"rate_limited_request could not resolve '{request_param}' "
                    f"for {handler.__qualname__}"
                )
                raise RuntimeError(msg)

            await enforce_request_rate_limit(
                cast("Request", request_obj),
                bucket=bucket,
                limit=limit,
                window_seconds=window_seconds,
                subject=subject,
                subject_resolver=subject_resolver,
                hash_subject=hash_subject,
                hash_secret=hash_secret,
                store_name=store_name,
                key_prefix=key_prefix,
                error_detail=error_detail,
                failure_mode=failure_mode,
            )
            return await handler(*args, **kwargs)

        setattr(wrapped, "__signature__", signature)
        return wrapped

    return decorator


__all__ = [
    "RateLimitFailureMode",
    "enforce_request_rate_limit",
    "rate_limited_request",
]
