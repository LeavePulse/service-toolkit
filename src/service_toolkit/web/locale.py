"""Locale negotiation helpers for HTTP services."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from litestar import Request


def _supported_locale_set(supported_locales: Iterable[str]) -> set[str]:
    return {
        str(locale or "").strip().lower()
        for locale in supported_locales
        if str(locale or "").strip()
    }


def _header_value(headers: object, name: str) -> str:
    getter = getattr(headers, "get", None)
    if callable(getter):
        value = getter(name)
        if value is not None:
            return str(value)
        value = getter(name.lower())
        if value is not None:
            return str(value)
    return ""


def normalize_locale_tag(
    value: str | None,
    supported_locales: Iterable[str],
) -> str | None:
    """Map a locale tag like ``uk-UA`` to a supported site locale code."""

    supported = _supported_locale_set(supported_locales)
    if not supported:
        return None

    raw = str(value or "").strip().lower().replace("_", "-")
    if not raw:
        return None
    if raw in supported:
        return raw

    primary = raw.split("-", 1)[0]
    if primary in supported:
        return primary
    return None


def resolve_locale_from_request(
    request: Request | None,
    *,
    supported_locales: Iterable[str],
    default_locale: str,
    query_param: str | None = None,
) -> str | None:
    """Resolve a preferred locale from query params, Accept-Language, or default."""

    supported = _supported_locale_set(supported_locales)
    if not supported:
        return None

    if request is not None:
        if query_param:
            explicit = request.query_params.get(query_param) or ""
            if locale := normalize_locale_tag(explicit, supported):
                return locale

        header = _header_value(request.headers, "Accept-Language")
        for part in header.split(","):
            raw = part.split(";", 1)[0]
            if locale := normalize_locale_tag(raw, supported):
                return locale

    return normalize_locale_tag(default_locale, supported)


__all__ = ["normalize_locale_tag", "resolve_locale_from_request"]
