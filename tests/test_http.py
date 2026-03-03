from __future__ import annotations

import pytest

from service_toolkit.http import build_shared_async_client, close_shared_async_clients


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
