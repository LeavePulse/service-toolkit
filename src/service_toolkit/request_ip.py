"""Helpers for resolving the real client IP behind reverse proxies."""

from __future__ import annotations

import re
from ipaddress import ip_address
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from litestar import Request


_FORWARDED_FOR_RE = re.compile(r"""for=(?:"?\[?)([^;\s,\]"]+)""", re.IGNORECASE)


def _normalize_ip_candidate(value: str | None) -> str | None:
    if not value:
        return None

    candidate = value.strip().strip('"').strip("'")
    if not candidate:
        return None

    if candidate.startswith("[") and candidate.endswith("]"):
        candidate = candidate[1:-1]

    try:
        normalized = ip_address(candidate)
    except ValueError:
        return None
    return str(normalized)


def _first_valid_csv_ip(value: str | None) -> str | None:
    if not value:
        return None

    for raw_part in value.split(","):
        if candidate := _normalize_ip_candidate(raw_part):
            return candidate
    return None


def _forwarded_header_ip(value: str | None) -> str | None:
    if not value:
        return None

    for match in _FORWARDED_FOR_RE.finditer(value):
        if candidate := _normalize_ip_candidate(match.group(1)):
            return candidate
    return None


def resolve_client_ip(request: Request) -> str | None:
    """Return the best-effort real client IP for a request.

    Priority:
    1. Cloudflare / proxy headers when present.
    2. The first value in ``X-Forwarded-For`` / ``Forwarded``.
    3. Litestar's socket-level ``request.client.host``.
    """

    headers = request.headers

    for header_name in ("cf-connecting-ip", "true-client-ip", "x-real-ip"):
        if candidate := _normalize_ip_candidate(headers.get(header_name)):
            return candidate

    if candidate := _first_valid_csv_ip(headers.get("x-forwarded-for")):
        return candidate

    if candidate := _forwarded_header_ip(headers.get("forwarded")):
        return candidate

    if request.client and request.client.host:
        return _normalize_ip_candidate(request.client.host)

    return None


__all__ = ["resolve_client_ip"]
