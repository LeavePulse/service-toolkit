"""Small HTTP auth header helpers shared by services."""

from __future__ import annotations

import hmac
from collections.abc import Mapping


def _header_value(headers: Mapping[str, object], name: str) -> str:
    expected = name.lower()
    for key, value in headers.items():
        if str(key).lower() == expected:
            return str(value)

    for candidate in (name, name.lower(), name.title()):
        value = headers.get(candidate)
        if value is not None:
            return str(value)
    return ""


def extract_bearer_token(
    headers: Mapping[str, object],
    *,
    header_name: str = "Authorization",
) -> str | None:
    """Return the bearer token from an HTTP headers mapping."""

    value = _header_value(headers, header_name).strip()
    scheme, separator, token = value.partition(" ")
    if not separator or scheme.lower() != "bearer":
        return None
    return token.strip() or None


def extract_internal_token(headers: Mapping[str, object]) -> str | None:
    """Return a service-to-service token from supported HTTP headers."""

    bearer = extract_bearer_token(headers)
    if bearer is not None:
        return bearer

    token = _header_value(headers, "X-Internal-Token").strip()
    return token or None


def internal_token_matches(
    headers: Mapping[str, object],
    expected_token: str | None,
) -> bool:
    """Constant-time comparison for service-to-service HTTP tokens."""

    expected = str(expected_token or "").strip()
    if not expected:
        return False

    provided = extract_internal_token(headers)
    return provided is not None and hmac.compare_digest(provided, expected)


__all__ = [
    "extract_bearer_token",
    "extract_internal_token",
    "internal_token_matches",
]
