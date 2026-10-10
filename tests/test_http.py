from __future__ import annotations

import pytest

from service_toolkit.web.http import build_shared_async_client, close_shared_async_clients


@pytest.mark.asyncio
async def test_build_shared_async_client_reuses_same_key_and_config() -> None:
    client_a = build_shared_async_client(
        key="tests.http.shared",
        base_url="https://example.com",
        timeout_seconds=5.0,
    )
    client_b = build_shared_async_client(
        key="tests.http.shared",
        base_url="https://example.com",
        timeout_seconds=5.0,
    )

    assert client_a is client_b

    await close_shared_async_clients("tests.http.shared")


@pytest.mark.asyncio
async def test_build_shared_async_client_rejects_conflicting_config() -> None:
    build_shared_async_client(
        key="tests.http.conflict",
        base_url="https://example.com",
        timeout_seconds=5.0,
    )

    with pytest.raises(RuntimeError):
        build_shared_async_client(
            key="tests.http.conflict",
            base_url="https://example.org",
            timeout_seconds=5.0,
        )

    await close_shared_async_clients("tests.http.conflict")


@pytest.mark.asyncio
async def test_close_shared_async_clients_closes_instances() -> None:
    client = build_shared_async_client(
        key="tests.http.close",
        timeout_seconds=5.0,
    )

    await close_shared_async_clients("tests.http.close")

    assert client.is_closed is True


@pytest.mark.asyncio
async def test_build_shared_async_client_applies_timeout_limits_and_retries() -> None:
    import httpx

    timeout = httpx.Timeout(connect=1.5, read=15.0, write=5.0, pool=2.0)
    limits = httpx.Limits(max_connections=8, max_keepalive_connections=4)
    client = build_shared_async_client(
        key="tests.http.transport",
        base_url="https://example.com",
        timeout=timeout,
        limits=limits,
        retries=1,
    )

    assert client.timeout == timeout
    pool = client._transport._pool
    assert pool._retries == 1
    assert pool._max_connections == 8
    assert pool._max_keepalive_connections == 4

    assert (
        build_shared_async_client(
            key="tests.http.transport",
            base_url="https://example.com",
            timeout=httpx.Timeout(connect=1.5, read=15.0, write=5.0, pool=2.0),
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
            retries=1,
        )
        is client
    )

    await close_shared_async_clients("tests.http.transport")


@pytest.mark.asyncio
async def test_build_shared_async_client_applies_limits_without_retries() -> None:
    import httpx

    client = build_shared_async_client(
        key="tests.http.limits",
        limits=httpx.Limits(max_connections=3, max_keepalive_connections=1),
    )

    pool = client._transport._pool
    assert pool._retries == 0
    assert pool._max_connections == 3

    await close_shared_async_clients("tests.http.limits")


@pytest.mark.asyncio
async def test_build_shared_async_client_rejects_different_retries() -> None:
    build_shared_async_client(key="tests.http.retries", retries=1)

    with pytest.raises(RuntimeError):
        build_shared_async_client(key="tests.http.retries", retries=2)

    await close_shared_async_clients("tests.http.retries")
