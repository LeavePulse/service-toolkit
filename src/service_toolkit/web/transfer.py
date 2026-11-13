"""How a service's responses travel, declared once.

A service states its transfer policy as data and the factory turns it into
middleware; handlers describe only what they return. The layers combine in a
fixed order so each sees what it needs: compression outside (it encodes the
final bytes and has nothing to do on a ``304``), conditional requests inside
(the validator is hashed from the raw representation, so one ``ETag`` covers
every encoding).

Install the ``compression`` extra (brotli) on any service behind Cloudflare:
Cloudflare asks origins for ``br, gzip`` only, so without it the origin
answers with gzip. zstd needs nothing (stdlib), but only clients that ask for
it directly get it.
"""

from __future__ import annotations

from dataclasses import dataclass

from litestar.middleware.base import DefineMiddleware

from .compression import CompressionMiddleware
from .etag import ETagMiddleware


@dataclass(frozen=True, slots=True)
class Transfer:
    """Transfer policy of one service.

    ``live`` are path regexes whose data differs on every request (online
    status, telemetry, aggregates): they get no validator but are still
    compressed, streams included. ``raw`` are path regexes never compressed.
    ``min_size`` is the smallest known body worth encoding;
    ``immutable_cache_bytes`` bounds memory for pre-compressed immutable assets
    (0 turns that cache off).
    """

    conditional: bool = True
    compress: bool = True
    live: tuple[str, ...] = ()
    raw: tuple[str, ...] = ()
    min_size: int = 1024
    immutable_cache_bytes: int = 64 * 1024 * 1024

    def middleware(self) -> list[DefineMiddleware]:
        layers: list[DefineMiddleware] = []
        if self.compress:
            layers.append(
                DefineMiddleware(
                    CompressionMiddleware,
                    min_size=self.min_size,
                    immutable_cache_bytes=self.immutable_cache_bytes,
                    exclude=self.raw,
                )
            )
        if self.conditional:
            layers.append(DefineMiddleware(ETagMiddleware, exclude=self.live))
        return layers


__all__ = ["Transfer"]
