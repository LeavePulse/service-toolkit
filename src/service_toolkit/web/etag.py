"""ETag / conditional-request middleware.

Stamps a strong ``ETag`` on cacheable ``GET`` responses (hashed from the
serialized body) and turns a matching ``If-None-Match`` into a ``304 Not
Modified`` with no body — so a client that already holds the resource only
pays for the round-trip, not the payload re-transfer + re-parse.

Scope: ``GET`` requests with a 2xx response and a body. A handler opts out by
setting ``Cache-Control: no-store`` (live endpoints — online status, telemetry,
aggregates — should do this; their data is never the same twice and must not be
cached). Non-GET, non-2xx, empty-body and no-store responses pass through
untouched.

The body is buffered to hash it; this is a deliberate trade for correctness on
a BFF, where the response is already assembled in memory from upstream gRPC.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from hashlib import blake2b

from litestar.types import (
    ASGIApp,
    HTTPResponseStartEvent,
    Message,
    Receive,
    Scope,
    Send,
)


def _header(headers: Iterable[tuple[bytes, bytes]], name: bytes) -> bytes | None:
    lowered = name.lower()
    for key, value in headers:
        if key.lower() == lowered:
            return value
    return None


def _compute_etag(body: bytes) -> str:
    # Strong ETag from the exact bytes the client would receive. blake2b is
    # faster than sha256 and 16 bytes of digest is ample for collision safety.
    return '"' + blake2b(body, digest_size=16).hexdigest() + '"'


class ETagMiddleware:
    """ASGI middleware adding ETag + If-None-Match handling to GET responses.

    ``exclude`` is a sequence of path regexes (matched against the request path)
    that skip tagging entirely — for live endpoints whose data changes every
    request (online status, telemetry, aggregates).
    """

    def __init__(self, app: ASGIApp, exclude: Sequence[str] = ()) -> None:
        self.app = app
        self._exclude = [re.compile(pattern) for pattern in exclude]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or scope.get("method") != "GET":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if any(pattern.search(path) for pattern in self._exclude):
            await self.app(scope, receive, send)
            return

        if_none_match = _header(scope["headers"], b"if-none-match")

        start_message: HTTPResponseStartEvent | None = None
        body_chunks: list[bytes] = []
        passthrough = False

        async def send_wrapper(message: Message) -> None:
            nonlocal start_message, passthrough

            if passthrough:
                await send(message)
                return

            if message["type"] == "http.response.start":
                status = int(message["status"])
                headers = list(message["headers"])
                cache_control = _header(headers, b"cache-control") or b""
                already_tagged = _header(headers, b"etag") is not None
                # Only buffer-and-tag a cacheable 2xx; otherwise stream as-is.
                if (
                    status < 200
                    or status >= 300
                    or already_tagged
                    or b"no-store" in cache_control.lower()
                ):
                    passthrough = True
                    await send(message)
                    return
                start_message = message
                return

            if message["type"] == "http.response.body":
                body_chunks.append(message.get("body", b""))
                if message.get("more_body", False):
                    return
                await self._finalize(
                    send, start_message, b"".join(body_chunks), if_none_match
                )
                return

            await send(message)

        await self.app(scope, receive, send_wrapper)

    async def _finalize(
        self,
        send: Send,
        start_message: HTTPResponseStartEvent | None,
        body: bytes,
        if_none_match: bytes | None,
    ) -> None:
        if start_message is None or not body:
            # No buffered start (shouldn't happen) or an empty body (204, etc.):
            # nothing meaningful to tag — emit what we have unchanged.
            if start_message is not None:
                await send(start_message)
            await send(
                {"type": "http.response.body", "body": body, "more_body": False}
            )
            return

        etag_bytes = _compute_etag(body).encode("latin-1")

        if if_none_match is not None and _etag_matches(if_none_match, etag_bytes):
            # Client's copy is current → 304 with no body, ETag echoed back.
            start_message["status"] = 304
            start_message["headers"] = [(b"etag", etag_bytes)]
            await send(start_message)
            await send(
                {"type": "http.response.body", "body": b"", "more_body": False}
            )
            return

        headers = list(start_message["headers"])
        headers.append((b"etag", etag_bytes))
        start_message["headers"] = headers
        await send(start_message)
        await send({"type": "http.response.body", "body": body, "more_body": False})


def _etag_matches(if_none_match: bytes, etag: bytes) -> bool:
    """Whether the client's ``If-None-Match`` covers our ETag.

    Handles ``*`` (any), comma-separated lists, and weak prefixes (``W/``);
    comparison is on the opaque tag value.
    """
    raw = if_none_match.strip()
    if raw == b"*":
        return True
    target = etag.lstrip(b"W/").strip()
    for candidate in raw.split(b","):
        if candidate.strip().lstrip(b"W/").strip() == target:
            return True
    return False


def etag_middleware(app: ASGIApp, exclude: Sequence[str] = ()) -> ASGIApp:
    """Return ETag middleware as an ASGI app factory."""
    return ETagMiddleware(app, exclude=exclude)


__all__ = ["ETagMiddleware", "etag_middleware"]
