from __future__ import annotations

from typing import Any, cast

import pytest
from litestar.exceptions import HTTPException

from service_toolkit.rate_limit import (
    RateLimitFailureMode,
    enforce_request_rate_limit,
    rate_limited_request,
)


class FakeRedisClient:
    def __init__(self) -> None:
        self.counters: dict[str, int] = {}

    async def eval(
        self,
        script: str,
        numkeys: int,
        key: str,
        ttl: int,
    ) -> int:
        _ = script, numkeys, ttl
        current = self.counters.get(key, 0) + 1
        self.counters[key] = current
        return current


class FakeRedisStore:
    def __init__(self) -> None:
        self._redis = FakeRedisClient()

    def _make_key(self, key: str) -> str:
        return f"LITESTAR:{key}"


class FakeUnsupportedStore:
    pass


class FakeApp:
    def __init__(self, store: object | None) -> None:
        self.stores = {"main": store} if store is not None else {}


class FakeClient:
    def __init__(self, host: str) -> None:
        self.host = host


class FakeRequest:
    def __init__(self, host: str = "127.0.0.1", store: object | None = None) -> None:
        self.client = FakeClient(host)
        self.app = FakeApp(store)


@pytest.mark.asyncio
async def test_enforce_request_rate_limit_blocks_after_limit() -> None:
    store = FakeRedisStore()
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
async def test_enforce_request_rate_limit_bypasses_when_backend_is_unsupported() -> None:
    request = cast(Any, FakeRequest(store=FakeUnsupportedStore()))

    await enforce_request_rate_limit(
        request,
        bucket="auth:bypass",
        limit=1,
        window_seconds=60,
        hash_subject=False,
        failure_mode=RateLimitFailureMode.BYPASS,
    )
    await enforce_request_rate_limit(
        request,
        bucket="auth:bypass",
        limit=1,
        window_seconds=60,
        hash_subject=False,
        failure_mode=RateLimitFailureMode.BYPASS,
    )


@pytest.mark.asyncio
async def test_enforce_request_rate_limit_raises_when_backend_is_unsupported() -> None:
    request = cast(Any, FakeRequest(store=FakeUnsupportedStore()))

    with pytest.raises(RuntimeError):
        await enforce_request_rate_limit(
            request,
            bucket="auth:raise",
            limit=1,
            window_seconds=60,
            hash_subject=False,
            failure_mode=RateLimitFailureMode.RAISE,
        )


@pytest.mark.asyncio
async def test_rate_limited_request_decorator_uses_request_param() -> None:
    store = FakeRedisStore()
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
    store = FakeRedisStore()
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
