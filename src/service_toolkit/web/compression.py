"""Content-negotiated response compression.

One middleware decides how every response body travels, so a handler only
describes data and never thinks about bytes on the wire:

* ``zstd`` (stdlib ``compression.zstd``), then ``br`` when the optional
  ``brotli`` package is installed, then ``gzip`` — chosen from the client's
  ``Accept-Encoding`` with q-values honoured (``identity`` and ``q=0`` too).
* Streams are compressed chunk by chunk and flushed at every chunk boundary, so
  SSE and NDJSON keep their latency: a client sees each event as soon as the
  handler sends it, it just costs fewer bytes.
* Responses marked ``immutable`` (hashed static assets) are compressed once at
  a high level and kept in a byte-bounded LRU; every later request pays a dict
  lookup instead of a compression pass.
* Bodies below ``min_size``, already-encoded bodies, ``no-transform`` and
  formats that are compressed by nature (images, video, archives, woff2) pass
  through untouched — compressing them only burns CPU.

It sits OUTSIDE the ETag middleware: the validator is hashed from the raw body,
a ``304`` has no body to compress, and an encoded response downgrades a strong
``ETag`` to weak (RFC 9110 §8.8.1: a strong validator names exact bytes, and
the bytes now differ per encoding). The ETag matcher ignores the ``W/`` prefix,
so revalidation keeps working across encodings.
"""

from __future__ import annotations

import gzip
import re
import zlib
from collections import OrderedDict
from collections.abc import Callable, Sequence
from compression import zstd
from dataclasses import dataclass, field
from types import ModuleType
from typing import Protocol

from litestar.types import (
    ASGIApp,
    HTTPResponseStartEvent,
    Message,
    Receive,
    Scope,
    Send,
)

from .asgi_headers import header

try:
    import brotli as _brotli
except ImportError:
    _brotli = None


class _Stream(Protocol):
    def chunk(self, data: bytes, *, last: bool) -> bytes: ...


class _Zstd:
    def __init__(self, level: int) -> None:
        self._c = zstd.ZstdCompressor(level=level)

    def chunk(self, data: bytes, *, last: bool) -> bytes:
        if last:
            return self._c.compress(data, zstd.ZstdCompressor.FLUSH_FRAME)
        return self._c.compress(data, zstd.ZstdCompressor.FLUSH_BLOCK)


class _Gzip:
    def __init__(self, level: int) -> None:
        self._c = zlib.compressobj(level, zlib.DEFLATED, 31)

    def chunk(self, data: bytes, *, last: bool) -> bytes:
        out = self._c.compress(data)
        return out + self._c.flush(zlib.Z_FINISH if last else zlib.Z_SYNC_FLUSH)


@dataclass(frozen=True, slots=True)
class _Codec:
    name: bytes
    stream: Callable[[int], _Stream]
    whole: Callable[[bytes, int], bytes]
    fast: int
    best: int


def _brotli_codec(module: ModuleType) -> _Codec:
    class _Brotli:
        def __init__(self, level: int) -> None:
            self._c = module.Compressor(quality=level)

        def chunk(self, data: bytes, *, last: bool) -> bytes:
            out: bytes = self._c.process(data)
            return out + (self._c.finish() if last else self._c.flush())

    def whole(data: bytes, level: int) -> bytes:
        out: bytes = module.compress(data, quality=level)
        return out

    return _Codec(b"br", _Brotli, whole, fast=4, best=11)


_CODECS: dict[bytes, _Codec] = {
    b"zstd": _Codec(
        b"zstd", _Zstd, lambda d, lvl: zstd.compress(d, level=lvl), fast=3, best=19
    ),
    b"gzip": _Codec(
        b"gzip", _Gzip, lambda d, lvl: gzip.compress(d, lvl, mtime=0), fast=5, best=9
    ),
}
if _brotli is not None:
    _CODECS[b"br"] = _brotli_codec(_brotli)

#: Server preference when the client weighs several encodings equally.
_PREFERENCE: tuple[bytes, ...] = (b"zstd", b"br", b"gzip")

#: Media types worth compressing: text in any form plus the structured
#: formats that are text underneath. Everything else is assumed to be
#: compressed already (images, video, audio, fonts in woff2, archives).
_COMPRESSIBLE = re.compile(
    rb"^(text/|application/(json|[\w.+-]+\+json|javascript|ecmascript|xml|"
    rb"[\w.+-]+\+xml|x-ndjson|ndjson|wasm|manifest\+json|graphql-response\+json)|"
    rb"image/svg\+xml|font/(ttf|otf)|application/vnd\.ms-fontobject)",
    re.IGNORECASE,
)


def negotiate(accept_encoding: bytes | None, available: Sequence[bytes]) -> bytes | None:
    """Pick the encoding for ``accept_encoding`` among ``available``.

    Returns ``None`` for identity: no header, nothing acceptable, or identity
    weighted above every codec we have.
    """
    if not accept_encoding:
        return None
    weights: dict[bytes, float] = {}
    for part in accept_encoding.lower().split(b","):
        token, _, params = part.strip().partition(b";")
        token = token.strip()
        if not token:
            continue
        q = 1.0
        for param in params.split(b";"):
            key, _, value = param.strip().partition(b"=")
            if key.strip() == b"q":
                try:
                    q = float(value)
                except ValueError:
                    q = 0.0
        weights[token] = q
    star = weights.get(b"*")
    best: bytes | None = None
    best_q = 0.0
    for name in _PREFERENCE:
        if name not in available:
            continue
        q = weights.get(name, star if star is not None else 0.0)
        if q > best_q:
            best, best_q = name, q
    identity_q = weights.get(b"identity", 0.001)
    if best is None or best_q < identity_q:
        return None
    return best


@dataclass(slots=True)
class _Cache:
    budget: int
    used: int = 0
    entries: OrderedDict[tuple[bytes, ...], bytes] = field(default_factory=OrderedDict)

    def get(self, key: tuple[bytes, ...]) -> bytes | None:
        value = self.entries.get(key)
        if value is not None:
            self.entries.move_to_end(key)
        return value

    def put(self, key: tuple[bytes, ...], value: bytes) -> None:
        if len(value) > self.budget // 4:
            return
        old = self.entries.pop(key, None)
        if old is not None:
            self.used -= len(old)
        self.entries[key] = value
        self.used += len(value)
        while self.used > self.budget and self.entries:
            _, dropped = self.entries.popitem(last=False)
            self.used -= len(dropped)


class CompressionMiddleware:
    """ASGI middleware compressing response bodies per ``Accept-Encoding``.

    ``min_size`` skips bodies whose ``Content-Length`` is smaller (a header and
    a frame cost more than they save). ``immutable_cache_bytes`` bounds the
    memory spent on pre-compressed immutable assets; 0 disables the cache.
    ``exclude`` takes path regexes that are never compressed.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        min_size: int = 1024,
        immutable_cache_bytes: int = 64 * 1024 * 1024,
        exclude: Sequence[str] = (),
    ) -> None:
        self.app = app
        self._min_size = min_size
        self.cache = _Cache(immutable_cache_bytes) if immutable_cache_bytes else None
        self._exclude = [re.compile(pattern) for pattern in exclude]
        self._available = tuple(_CODECS)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or scope.get("method") == "HEAD":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if any(pattern.search(path) for pattern in self._exclude):
            await self.app(scope, receive, send)
            return

        encoding = negotiate(
            header(scope["headers"], b"accept-encoding"), self._available
        )
        response = _Response(
            send,
            _CODECS[encoding] if encoding else None,
            self,
            str(path).encode(),
        )
        await self.app(scope, receive, response.send)

    def eligible(self, status: int, headers: list[tuple[bytes, bytes]]) -> bool:
        if status < 200 or status in (204, 206, 304):
            return False
        if header(headers, b"content-encoding") is not None:
            return False
        if b"no-transform" in (header(headers, b"cache-control") or b"").lower():
            return False
        content_type = header(headers, b"content-type")
        if content_type is None or not _COMPRESSIBLE.match(content_type.strip()):
            return False
        length = header(headers, b"content-length")
        return not (length is not None and length.isdigit() and int(length) < self._min_size)


class _Response:
    """Encoding state of one response, driven by its ASGI messages."""

    def __init__(
        self,
        send: Send,
        codec: _Codec | None,
        owner: CompressionMiddleware,
        path: bytes,
    ) -> None:
        self._send = send
        self._codec = codec
        self._owner = owner
        self._path = path
        self._passthrough = False
        self._active: tuple[_Codec, HTTPResponseStartEvent] | None = None
        self._cached: tuple[_Cache, tuple[bytes, ...]] | None = None
        self._hit: bytes | None = None
        self._buffered: list[bytes] = []
        self._stream: _Stream | None = None

    async def send(self, message: Message) -> None:
        if self._passthrough:
            await self._send(message)
        elif message["type"] == "http.response.start":
            await self._start(message)
        elif message["type"] == "http.response.body" and self._active is not None:
            codec, start = self._active
            await self._body(
                codec, start, message.get("body", b""), message.get("more_body", False)
            )
        else:
            await self._send(message)

    async def _start(self, message: HTTPResponseStartEvent) -> None:
        headers = list(message["headers"])
        if not self._owner.eligible(int(message["status"]), headers):
            self._passthrough = True
            await self._send(message)
            return
        message["headers"] = _with_vary(headers)
        if self._codec is None:
            self._passthrough = True
            await self._send(message)
            return
        self._active = (self._codec, message)
        cache = self._owner.cache
        cache_control = (header(headers, b"cache-control") or b"").lower()
        if cache is not None and b"immutable" in cache_control:
            key = (
                self._codec.name,
                self._path,
                header(headers, b"etag") or b"",
                header(headers, b"last-modified") or b"",
                header(headers, b"content-length") or b"",
            )
            self._cached = (cache, key)
            self._hit = cache.get(key)

    async def _body(
        self, codec: _Codec, start: HTTPResponseStartEvent, body: bytes, more: bool
    ) -> None:
        if self._hit is not None:
            if not more:
                await self._whole(codec, start, self._hit)
            return
        if self._cached is not None:
            self._buffered.append(body)
            if more:
                return
            cache, key = self._cached
            packed = codec.whole(b"".join(self._buffered), codec.best)
            cache.put(key, packed)
            await self._whole(codec, start, packed)
            return
        if self._stream is None:
            if not more:
                packed = codec.whole(body, codec.fast)
                if len(packed) >= len(body):
                    await self._send(start)
                    await self._send(
                        {"type": "http.response.body", "body": body, "more_body": False}
                    )
                    return
                await self._whole(codec, start, packed)
                return
            self._stream = codec.stream(codec.fast)
            await self._send(_encoded_start(start, codec.name, None))
        await self._send(
            {
                "type": "http.response.body",
                "body": self._stream.chunk(body, last=not more),
                "more_body": more,
            }
        )

    async def _whole(
        self, codec: _Codec, start: HTTPResponseStartEvent, packed: bytes
    ) -> None:
        await self._send(_encoded_start(start, codec.name, len(packed)))
        await self._send({"type": "http.response.body", "body": packed, "more_body": False})


def _with_vary(headers: list[tuple[bytes, bytes]]) -> list[tuple[bytes, bytes]]:
    for index, (key, value) in enumerate(headers):
        if key.lower() == b"vary":
            if b"accept-encoding" in value.lower() or value.strip() == b"*":
                return headers
            headers[index] = (key, value + b", Accept-Encoding")
            return headers
    headers.append((b"vary", b"Accept-Encoding"))
    return headers


def _encoded_start(
    start: HTTPResponseStartEvent, encoding: bytes, length: int | None
) -> HTTPResponseStartEvent:
    headers: list[tuple[bytes, bytes]] = []
    for key, value in start["headers"]:
        lowered = key.lower()
        if lowered == b"content-length":
            continue
        if lowered == b"etag" and not value.startswith(b"W/"):
            value = b"W/" + value
        headers.append((key, value))
    headers.append((b"content-encoding", encoding))
    if length is not None:
        headers.append((b"content-length", str(length).encode()))
    return {**start, "headers": headers}


def available_encodings() -> tuple[str, ...]:
    """Encodings this process can produce, in server preference order."""
    return tuple(name.decode() for name in _PREFERENCE if name in _CODECS)


__all__ = [
    "CompressionMiddleware",
    "available_encodings",
    "negotiate",
]
