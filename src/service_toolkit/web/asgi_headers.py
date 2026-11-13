"""Header lookups on raw ASGI header lists, shared by the transfer middlewares."""

from __future__ import annotations

from collections.abc import Iterable


def header(headers: Iterable[tuple[bytes, bytes]], name: bytes) -> bytes | None:
    """First value of ``name`` (case-insensitive) in an ASGI header list."""
    lowered = name.lower()
    for key, value in headers:
        if key.lower() == lowered:
            return value
    return None


__all__ = ["header"]
