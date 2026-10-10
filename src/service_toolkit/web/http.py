"""Shared httpx client helpers."""

from __future__ import annotations

from collections.abc import Mapping
from threading import Lock
from typing import Any

import httpx

_CLIENT_LOCK = Lock()
_SHARED_CLIENTS: dict[str, httpx.AsyncClient] = {}
_SHARED_CLIENT_SPECS: dict[str, tuple[object, ...]] = {}


def _normalize_headers(
    headers: Mapping[str, str] | None,
) -> tuple[tuple[str, str], ...]:
    if not headers:
        return ()
    return tuple(sorted((str(key), str(value)) for key, value in headers.items()))


def build_shared_async_client(
    *,
    key: str,
    base_url: str | None = None,
    timeout_seconds: float = 10.0,
    headers: Mapping[str, str] | None = None,
    follow_redirects: bool = False,
    timeout: httpx.Timeout | None = None,
    limits: httpx.Limits | None = None,
    retries: int = 0,
) -> httpx.AsyncClient:
    """Return a process-wide shared AsyncClient for the given configuration.

    ``timeout`` takes precedence over ``timeout_seconds`` when a service needs
    separate connect/read/write/pool budgets. ``limits`` sizes the connection
    pool. ``retries`` retries failed connection attempts (httpx transport
    retries: connect errors only, never a request that reached the server).
    """
    normalized_key = str(key).strip()
    if not normalized_key:
        msg = "Shared AsyncClient key must not be empty."
        raise ValueError(msg)

    normalized_base_url = str(base_url or "").strip() or None
    normalized_headers = _normalize_headers(headers)
    resolved_timeout = timeout or httpx.Timeout(float(timeout_seconds))
    resolved_retries = max(0, int(retries))
    spec = (
        normalized_base_url,
        resolved_timeout,
        normalized_headers,
        bool(follow_redirects),
        limits,
        resolved_retries,
    )

    with _CLIENT_LOCK:
        existing = _SHARED_CLIENTS.get(normalized_key)
        existing_spec = _SHARED_CLIENT_SPECS.get(normalized_key)
        if existing is not None:
            if existing_spec != spec:
                msg = (
                    "Shared AsyncClient key was reused with different configuration: "
                    f"{normalized_key}"
                )
                raise RuntimeError(msg)
            return existing

        options: dict[str, Any] = {
            "timeout": resolved_timeout,
            "headers": dict(normalized_headers),
            "follow_redirects": bool(follow_redirects),
        }
        if normalized_base_url is not None:
            options["base_url"] = normalized_base_url
        if resolved_retries:
            # A custom transport owns the pool, so the limits move onto it;
            # httpx ignores ``limits`` on the client once a transport is given.
            options["transport"] = httpx.AsyncHTTPTransport(
                retries=resolved_retries,
                limits=limits or httpx.Limits(
                    max_connections=100, max_keepalive_connections=20
                ),
            )
        elif limits is not None:
            options["limits"] = limits
        client = httpx.AsyncClient(**options)  # noqa: archlint=ad-hoc-client
        _SHARED_CLIENTS[normalized_key] = client
        _SHARED_CLIENT_SPECS[normalized_key] = spec
        return client


async def close_shared_async_clients(*keys: str) -> None:
    """Close one or more shared AsyncClient instances."""
    normalized_keys = [str(key).strip() for key in keys if str(key).strip()]
    with _CLIENT_LOCK:
        if normalized_keys:
            clients = [
                (key, _SHARED_CLIENTS.pop(key, None)) for key in normalized_keys
            ]
            for key in normalized_keys:
                _SHARED_CLIENT_SPECS.pop(key, None)
        else:
            clients = list(_SHARED_CLIENTS.items())
            _SHARED_CLIENTS.clear()
            _SHARED_CLIENT_SPECS.clear()

    for _key, client in clients:
        if client is not None:
            await client.aclose()


__all__ = ["build_shared_async_client", "close_shared_async_clients"]
