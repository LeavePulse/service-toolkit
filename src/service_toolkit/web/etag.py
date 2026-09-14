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


#: The caching policy a tagged response carries when its handler names none.
#:
#: Stamping a validator without a policy is the half-answer that caused this to
#: be written: a response with an ``ETag`` and no ``Cache-Control`` is one a
#: cache MAY store on a heuristic (RFC 9111 §4.2.2), and every cache that does
#: starts keeping a validator of its own. A client whose SDK also keeps one then
#: has two, and the client's own store can be handed a ``304`` for a body it
#: never held — a resource that exists, read as absent.
#:
#: ``private`` because every response on these services is answered for ONE
#: bearer: an operator's graph is not an intermediary's to hold, and the panel's
#: same-origin proxy is exactly such an intermediary. ``no-cache`` rather than
#: ``no-store`` because the validator is still worth having — ``no-cache``
#: permits storing and requires revalidating, which is what an ETag is FOR.
#: ``no-store`` is reserved for the opt-out above, where it already means
#: something else: do not tag this at all.
#:
#: A default, not an override: a handler that states its own policy keeps it.
_DEFAULT_CACHE_CONTROL = b"private, no-cache"

#: Header fields a ``304`` repeats from the ``200`` it stands in for.
#:
#: RFC 9110 §15.4.5 names exactly these: a cache updates its stored response
#: from a ``304``, so a field that would have differed on the ``200`` has to
#: travel with it or the stored copy keeps a stale one. ``Cache-Control`` is the
#: one that bites hardest by its ABSENCE — a response with no caching policy
#: invites a heuristic (RFC 9111 §4.2.2), and a client that ends up with its own
#: validator alongside the one its SDK keeps has two caches answering for one
#: URL.
#:
#: A table rather than a condition per field, because "which fields survive a
#: 304" is one decision and the failure from getting it wrong is silent: the
#: response is well-formed either way, and what breaks is a cache two hops away.
#:
#: Deliberately NOT a pass-through of everything: ``Content-Length`` and
#: ``Content-Type`` describe a body that a 304 does not carry, and repeating
#: them has clients waiting for bytes that never come.
_CARRIED_ON_304: frozenset[bytes] = frozenset(
    {
        b"cache-control",
        b"content-location",
        b"date",
        b"expires",
        b"vary",
    }
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
            await send({"type": "http.response.body", "body": body, "more_body": False})
            return

        etag_bytes = _compute_etag(body).encode("latin-1")

        # The policy is settled BEFORE the two paths diverge, so both carry it.
        #
        # Stamped only on the 200 it reached a client exactly once: a 304 repeats
        # the fields the response already had, and a default added after the
        # branch is not one of them. Measured on the live fleet — the 200 came
        # back `private, no-cache` and the 304 to the same URL carried `date` and
        # `etag` alone, which is the "no policy at all" a heuristic cache acts on.
        # The second response is the one that reaches a cache MOST often.
        headers = list(start_message["headers"])
        if _header(headers, b"cache-control") is None:
            headers.append((b"cache-control", _DEFAULT_CACHE_CONTROL))
        start_message["headers"] = headers

        if if_none_match is not None and _etag_matches(if_none_match, etag_bytes):
            # Client's copy is current → 304 with no body, ETag echoed back.
            #
            # The other headers are CARRIED, not dropped. RFC 9110 §15.4.5 requires
            # a 304 to repeat the fields a 200 to the same request would have sent
            # — Cache-Control, Vary, Date, Content-Location, Expires — because a
            # cache validates its stored response against them. A 304 stripped to
            # a bare ETag tells every cache on the path that the response has no
            # caching policy at all, and the ones that then apply a heuristic
            # (RFC 9111 §4.2.2) become a second cache holding a second validator
            # for the same URL. Two caches, two validators, and the client's own
            # store can be handed a 304 for a body it does not have.
            carried = [
                (key, value)
                for key, value in start_message["headers"]
                if key.lower() in _CARRIED_ON_304
            ]
            carried.append((b"etag", etag_bytes))
            start_message["status"] = 304
            start_message["headers"] = carried
            await send(start_message)
            await send({"type": "http.response.body", "body": b"", "more_body": False})
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
