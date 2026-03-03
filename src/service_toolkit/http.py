"""Shared httpx client helpers."""

from __future__ import annotations

from collections.abc import Mapping
from threading import Lock

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
) -> httpx.AsyncClient:
    """Return a process-wide shared AsyncClient for the given configuration."""
    normalized_key = str(key).strip()
    if not normalized_key:
        msg = "Shared AsyncClient key must not be empty."
        raise ValueError(msg)

    normalized_base_url = str(base_url or "").strip() or None
    normalized_headers = _normalize_headers(headers)
    spec = (
        normalized_base_url,
        float(timeout_seconds),
        normalized_headers,
        bool(follow_redirects),
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

        if normalized_base_url is None:
            client = httpx.AsyncClient(
                timeout=float(timeout_seconds),
                headers=dict(normalized_headers),
                follow_redirects=bool(follow_redirects),
            )
        else:
            client = httpx.AsyncClient(
                base_url=normalized_base_url,
                timeout=float(timeout_seconds),
                headers=dict(normalized_headers),
                follow_redirects=bool(follow_redirects),
            )
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
