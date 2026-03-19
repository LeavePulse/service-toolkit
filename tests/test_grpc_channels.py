from __future__ import annotations

import asyncio

import pytest

from service_toolkit.grpc import channels as grpc_channels


class _FakeUnaryUnaryCallable:
    def __init__(self, channel_id: int) -> None:
        self._channel_id = channel_id

    async def __call__(self, request: object, **kwargs: object) -> int:
        return self._channel_id


class _FakeChannel:
    def __init__(self, channel_id: int) -> None:
        self.channel_id = channel_id
        self.closed = False

    def unary_unary(self, *args: object, **kwargs: object) -> _FakeUnaryUnaryCallable:
        return _FakeUnaryUnaryCallable(self.channel_id)

    def unary_stream(self, *args: object, **kwargs: object) -> _FakeUnaryUnaryCallable:
        return _FakeUnaryUnaryCallable(self.channel_id)

    def stream_unary(self, *args: object, **kwargs: object) -> _FakeUnaryUnaryCallable:
        return _FakeUnaryUnaryCallable(self.channel_id)

    def stream_stream(self, *args: object, **kwargs: object) -> _FakeUnaryUnaryCallable:
        return _FakeUnaryUnaryCallable(self.channel_id)

    async def channel_ready(self) -> None:
        return None

    def get_state(self, try_to_connect: bool = False) -> str:
        return "ready" if try_to_connect else "idle"

    async def wait_for_state_change(self, last_observed_state: object) -> None:
        return None

    async def close(self, grace: float | None = None) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _reset_shared_channels() -> None:
    grpc_channels._SHARED_CHANNELS.clear()  # noqa: SLF001
    grpc_channels._SHARED_CHANNEL_SPECS.clear()  # noqa: SLF001


def test_build_shared_channel_reuses_real_channel_within_same_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[_FakeChannel] = []

    def _fake_insecure_channel(
        target: str,
        *,
        options: object = None,
        interceptors: object = None,
    ) -> _FakeChannel:
        channel = _FakeChannel(len(created) + 1)
        created.append(channel)
        return channel

    monkeypatch.setattr(
        grpc_channels.grpc.aio,
        "insecure_channel",
        _fake_insecure_channel,
    )

    channel = grpc_channels.build_shared_channel(
        key="test.same-loop",
        target="server-service:50201",
        token="secret",
    )

    async def _use_channel() -> tuple[int, int]:
        call = channel.unary_unary("/leavepulse.test.v1.ExampleService/GetOne")
        return await call(object()), await call(object())

    result = asyncio.run(_use_channel())

    assert result == (1, 1)
    assert len(created) == 1


def test_build_shared_channel_creates_real_channels_per_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[_FakeChannel] = []

    def _fake_insecure_channel(
        target: str,
        *,
        options: object = None,
        interceptors: object = None,
    ) -> _FakeChannel:
        channel = _FakeChannel(len(created) + 1)
        created.append(channel)
        return channel

    monkeypatch.setattr(
        grpc_channels.grpc.aio,
        "insecure_channel",
        _fake_insecure_channel,
    )

    channel = grpc_channels.build_shared_channel(
        key="test.per-loop",
        target="monitoring-service:50200",
    )

    async def _use_channel() -> int:
        call = channel.unary_unary("/leavepulse.test.v1.ExampleService/GetOne")
        return await call(object())

    first = asyncio.run(_use_channel())
    second = asyncio.run(_use_channel())

    assert first == 1
    assert second == 2
    assert len(created) == 2


def test_close_shared_channels_closes_created_channels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[_FakeChannel] = []

    def _fake_insecure_channel(
        target: str,
        *,
        options: object = None,
        interceptors: object = None,
    ) -> _FakeChannel:
        channel = _FakeChannel(len(created) + 1)
        created.append(channel)
        return channel

    monkeypatch.setattr(
        grpc_channels.grpc.aio,
        "insecure_channel",
        _fake_insecure_channel,
    )

    channel = grpc_channels.build_shared_channel(
        key="test.close",
        target="gateway-ingest:50300",
    )

    async def _use_and_close() -> None:
        call = channel.unary_unary("/leavepulse.test.v1.ExampleService/GetOne")
        assert await call(object()) == 1
        await grpc_channels.close_shared_channels("test.close")

    asyncio.run(_use_and_close())

    assert len(created) == 1
    assert created[0].closed is True
    assert "test.close" not in grpc_channels._SHARED_CHANNELS  # noqa: SLF001


def test_build_shared_channel_uses_stable_keepalive_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_options: list[dict[str, int]] = []

    def _fake_insecure_channel(
        target: str,
        *,
        options: object = None,
        interceptors: object = None,
    ) -> _FakeChannel:
        del target, interceptors
        captured_options.append(dict(options or []))
        return _FakeChannel(1)

    monkeypatch.setattr(
        grpc_channels.grpc.aio,
        "insecure_channel",
        _fake_insecure_channel,
    )

    channel = grpc_channels.build_shared_channel(
        key="test.keepalive-options",
        target="server-service:50201",
    )

    async def _use_channel() -> int:
        call = channel.unary_unary("/leavepulse.test.v1.ExampleService/GetOne")
        return await call(object())

    assert asyncio.run(_use_channel()) == 1
    assert captured_options == [
        {
            "grpc.keepalive_time_ms": 300_000,
            "grpc.keepalive_timeout_ms": 10_000,
            "grpc.keepalive_permit_without_calls": 0,
            "grpc.max_receive_message_length": 16 * 1024 * 1024,
        }
    ]
