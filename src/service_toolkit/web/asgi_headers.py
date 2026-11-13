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


def header_list(headers: Iterable[tuple[bytes, bytes]], name: bytes) -> bytes | None:
    """All values of a list-valued header joined with ``, `` (RFC 9110 §5.3).

    ``Accept-Encoding`` and ``Cache-Control`` may arrive split over several
    fields; reading only the first would miss directives in the others.
    """
    lowered = name.lower()
    values = [value for key, value in headers if key.lower() == lowered]
    return b", ".join(values) if values else None


__all__ = ["header", "header_list"]
