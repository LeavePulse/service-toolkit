from __future__ import annotations

import pytest

import service_toolkit.grpc.client as grpc_client


class _Stub:
    def __init__(self, channel: object) -> None:
        self.channel = channel


def test_grpc_client_builds_shared_channel_and_stub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    channel = object()

    def _build_shared_channel(**kwargs: object) -> object:
        captured.update(kwargs)
        return channel

    monkeypatch.setattr(grpc_client, "build_shared_channel", _build_shared_channel)

    client = grpc_client.build_grpc_client(
        key="test.auth",
        target="auth-service:50000",
        token="secret",
        timeout_seconds=3.5,
    )

    stub = client.stub(_Stub)

    assert stub.channel is channel
    assert client.channel is channel
    assert client.timeout_seconds == 3.5
    assert captured == {
        "key": "test.auth",
        "target": "auth-service:50000",
        "token": "secret",
        "service_name": None,
    }


@pytest.mark.asyncio
async def test_grpc_client_call_uses_default_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        grpc_client,
        "build_shared_channel",
        lambda **_: object(),
    )
    client = grpc_client.build_grpc_client(
        key="test.call",
        target="server-service:50051",
        timeout_seconds=2.0,
    )
    captured: dict[str, object] = {}

    async def _method(request: object, *, timeout: float) -> str:
        captured["request"] = request
        captured["timeout"] = timeout
        return "ok"

    result = await client.call(_method, "request", resource="server")

    assert result == "ok"
    assert captured == {"request": "request", "timeout": 2.0}


@pytest.mark.asyncio
async def test_grpc_client_close_closes_own_channel_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        grpc_client,
        "build_shared_channel",
        lambda **_: object(),
    )
    closed: list[str] = []

    async def _close_shared_channels(*keys: str) -> None:
        closed.extend(keys)

    monkeypatch.setattr(grpc_client, "close_shared_channels", _close_shared_channels)

    client = grpc_client.build_grpc_client(
        key="test.close",
        target="monitoring-service:50051",
        timeout_seconds=1.0,
    )

    await client.close()

    assert closed == ["test.close"]


def test_grpc_client_rejects_non_positive_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        grpc_client,
        "build_shared_channel",
        lambda **_: object(),
    )

    with pytest.raises(ValueError, match="timeout_seconds"):
        grpc_client.build_grpc_client(
            key="test.bad-timeout",
            target="auth-service:50000",
            timeout_seconds=0,
        )
