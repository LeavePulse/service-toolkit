from __future__ import annotations

from typing import Any, cast

import pytest
from litestar.exceptions import HTTPException

from service_toolkit.rate_limit import enforce_request_rate_limit, rate_limited_request


class FakeStore:
    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}

    async def get(self, key: str) -> bytes | None:
        return self.data.get(key)

    async def set(self, key: str, value: bytes, *, expires_in: int) -> None:
        _ = expires_in
        self.data[key] = value


class FakeApp:
    def __init__(self, store: FakeStore | None) -> None:
        self.stores = {"main": store} if store is not None else {}


class FakeClient:
    def __init__(self, host: str) -> None:
        self.host = host


class FakeRequest:
    def __init__(self, host: str = "127.0.0.1", store: FakeStore | None = None) -> None:
        self.client = FakeClient(host)
        self.app = FakeApp(store)


@pytest.mark.asyncio
async def test_enforce_request_rate_limit_blocks_after_limit() -> None:
    store = FakeStore()
    request = cast(Any, FakeRequest(store=store))

    await enforce_request_rate_limit(
        request,
        bucket="auth:login",
        limit=2,
        window_seconds=60,
        hash_subject=False,
    )
    await enforce_request_rate_limit(
        request,
        bucket="auth:login",
        limit=2,
        window_seconds=60,
        hash_subject=False,
    )
    with pytest.raises(HTTPException) as exc:
        await enforce_request_rate_limit(
            request,
            bucket="auth:login",
            limit=2,
            window_seconds=60,
            hash_subject=False,
        )
    assert exc.value.status_code == 429


@pytest.mark.asyncio
async def test_enforce_request_rate_limit_uses_local_fallback_without_store() -> None:
    request = cast(Any, FakeRequest(store=None))
    await enforce_request_rate_limit(
        request,
        bucket="auth:register:local-fallback",
        limit=1,
        window_seconds=60,
        hash_subject=False,
    )

    with pytest.raises(HTTPException) as exc:
        await enforce_request_rate_limit(
            request,
            bucket="auth:register:local-fallback",
            limit=1,
            window_seconds=60,
            hash_subject=False,
        )
    assert exc.value.status_code == 429


@pytest.mark.asyncio
async def test_enforce_request_rate_limit_local_fallback_uses_subject_hash() -> None:
    request = cast(Any, FakeRequest(store=None))
    await enforce_request_rate_limit(
        request,
        bucket="auth:register:local-hash",
        limit=1,
        window_seconds=60,
        hash_secret="pepper",
    )
    with pytest.raises(HTTPException) as exc:
        await enforce_request_rate_limit(
            request,
            bucket="auth:register:local-hash",
            limit=1,
            window_seconds=60,
            hash_secret="pepper",
        )
    assert exc.value.status_code == 429


@pytest.mark.asyncio
async def test_rate_limited_request_decorator_uses_request_param() -> None:
    store = FakeStore()
    request = cast(Any, FakeRequest(store=store))
    calls: list[int] = []

    @rate_limited_request(
        bucket="auth:refresh",
        limit=1,
        window_seconds=60,
        hash_subject=False,
    )
    async def handler(request: Any, value: int) -> int:
        calls.append(value)
        return value

    assert await handler(request, 7) == 7
    assert calls == [7]

    with pytest.raises(HTTPException) as exc:
        await handler(request, 9)
    assert exc.value.status_code == 429


@pytest.mark.asyncio
async def test_rate_limited_request_decorator_works_for_method_handlers() -> None:
    store = FakeStore()
    request = cast(Any, FakeRequest(store=store))

    class DemoController:
        @rate_limited_request(
            bucket="billing:checkout",
            limit=1,
            window_seconds=60,
            hash_subject=False,
        )
        async def create(self, request: Any, value: int) -> int:
            _ = self
            return value

    controller = DemoController()
    assert await controller.create(request, 42) == 42

    with pytest.raises(HTTPException) as exc:
        await controller.create(request, 43)
    assert exc.value.status_code == 429
