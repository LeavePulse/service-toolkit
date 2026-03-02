"""Reusable Redis-backed request rate limiting helpers."""

from __future__ import annotations

import inspect
import re
from asyncio import Lock
from functools import wraps
from hashlib import blake2b
from time import monotonic
from typing import TYPE_CHECKING, ParamSpec, TypeVar, cast

from litestar import status_codes
from litestar.exceptions import HTTPException

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from litestar import Request


P = ParamSpec("P")
R = TypeVar("R")

_BUCKET_PART_RE = re.compile(r"[^a-z0-9:_-]+")
_SUBJECT_PART_RE = re.compile(r"[^a-z0-9:_-]+")
_FALLBACK_SUBJECT = "unknown"
_LOCAL_COUNTERS: dict[str, tuple[int, float]] = {}
_LOCAL_COUNTERS_LOCK = Lock()


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


def _parse_counter(raw: object) -> int:
    if isinstance(raw, (bytes, bytearray)):
        value = raw.decode(errors="ignore").strip()
    elif isinstance(raw, str):
        value = raw.strip()
    else:
        return 0

    try:
        return max(0, int(value))
    except ValueError:
        return 0


def _default_subject_resolver(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host
    return _FALLBACK_SUBJECT


async def _enforce_local_rate_limit(
    *,
    key: str,
    limit: int,
    window_seconds: int,
    error_detail: str,
) -> None:
    now = monotonic()
    ttl = max(1, int(window_seconds))
    expires_at = now + ttl

    async with _LOCAL_COUNTERS_LOCK:
        # Opportunistic cleanup of expired entries.
        for stale_key, (_, stale_expires_at) in list(_LOCAL_COUNTERS.items()):
            if stale_expires_at <= now:
                _LOCAL_COUNTERS.pop(stale_key, None)

        current_count = 0
        current_expires_at = expires_at
        current = _LOCAL_COUNTERS.get(key)
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

        _LOCAL_COUNTERS[key] = (current_count + 1, current_expires_at)


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
) -> None:
    """Limit repeated requests for a bucket/subject pair.

    The helper uses ``request.app.stores[store_name]`` (typically Redis-backed).
    If the store is unavailable, the limiter is skipped to avoid hard failures.
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

    store = request.app.stores.get(store_name)
    if store is None:
        await _enforce_local_rate_limit(
            key=key,
            limit=limit,
            window_seconds=window_seconds,
            error_detail=error_detail,
        )
        return

    current = _parse_counter(await store.get(key))
    if current >= limit:
        raise HTTPException(
            status_code=status_codes.HTTP_429_TOO_MANY_REQUESTS,
            detail=error_detail,
        )

    await store.set(
        key,
        str(current + 1).encode(),
        expires_in=max(1, int(window_seconds)),
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
            )
            return await handler(*args, **kwargs)

        setattr(wrapped, "__signature__", signature)
        return wrapped

    return decorator
