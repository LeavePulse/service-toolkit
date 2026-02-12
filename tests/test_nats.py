"""Tests for NATS helpers."""

from __future__ import annotations

import importlib
import os
import sys
import types
from types import SimpleNamespace
from typing import Mapping

import pytest

from service_toolkit import nats as nats_helpers
from service_toolkit.nats import NATSClient, NATSSettings


class DummyConnection:
    def __init__(self) -> None:
        self.is_connected = True
        self.published: list[tuple[str, bytes, dict[str, str]]] = []
        self.requests: list[tuple[str, bytes, dict[str, str] | None]] = []
        self._jetstream = DummyJetStream()

    async def publish(self, subject: str, payload: bytes, headers: dict[str, str] | None = None) -> None:
        self.published.append((subject, payload, dict(headers or {})))

    async def request(
        self,
        subject: str,
        payload: bytes,
        *,
        timeout: float,
        headers: dict[str, str] | None = None,
    ) -> SimpleNamespace:
        self.requests.append((subject, payload, dict(headers or {})))
        return SimpleNamespace(data=b"reply", subject=subject, headers=headers or {})

    async def drain(self) -> None:
        self.is_connected = False

    async def close(self) -> None:
        self.is_connected = False

    def jetstream(self, *, domain: str | None = None) -> "DummyJetStream":
        self._jetstream.last_domain = domain
        return self._jetstream


class DummyJetStream:
    def __init__(self) -> None:
        self.streams: dict[str, object] = {}
        self.consumers: dict[tuple[str, str], object] = {}
        self.add_stream_calls: list[object] = []
        self.add_consumer_calls: list[tuple[str, object]] = []
        self.last_domain: str | None = None

    async def stream_info(self, name: str) -> object:
        if name not in self.streams:
            raise nats_helpers.NotFoundError()  # pragma: no cover - expected in tests
        return self.streams[name]

    async def add_stream(self, config: object) -> object:
        self.add_stream_calls.append(config)
        self.streams[getattr(config, "name")] = config
        return config

    async def consumer_info(self, stream: str, durable: str) -> object:
        key = (stream, durable)
        if key not in self.consumers:
            raise nats_helpers.NotFoundError()
        return self.consumers[key]

    async def add_consumer(self, stream: str, config: object) -> object:
        self.add_consumer_calls.append((stream, config))
        key = (stream, getattr(config, "durable_name"))
        self.consumers[key] = config
        return config


@pytest.mark.asyncio
async def test_client_publish_and_request(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = DummyConnection()

    async def fake_connect(**_: object) -> DummyConnection:
        return connection

    monkeypatch.setattr(nats_helpers, "connect", fake_connect)

    client = NATSClient(NATSSettings())
    await client.publish("demo.subject", b"payload")
    await client.publish_json("demo.subject", {"key": "value"})
    response = await client.request("demo.request", b"data")

    assert len(connection.published) == 2
    assert connection.published[0][0] == "demo.subject"
    assert connection.published[1][1].decode("utf-8") == "{\"key\": \"value\"}"
    assert response.data == b"reply"

    await client.close()
    assert not connection.is_connected


def test_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NATS_SERVERS", "nats://localhost:4223 nats://demo:4224")
    monkeypatch.setenv("NATS_NAME", "server-service")
    monkeypatch.setenv("NATS_MAX_RECONNECT_ATTEMPTS", "10")
    monkeypatch.setenv("NATS_RECONNECT_TIME_WAIT", "1.5")
    monkeypatch.setenv("NATS_PING_INTERVAL", "30")

    settings = NATSSettings.from_env()

    assert settings.servers == ("nats://localhost:4223", "nats://demo:4224")
    assert settings.name == "server-service"
    assert settings.max_reconnect_attempts == 10
    assert settings.reconnect_time_wait == pytest.approx(1.5)
    assert settings.ping_interval == pytest.approx(30.0)


def test_from_env_with_env_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeBaseSettings:
        def __init__(self, **values) -> None:
            for key, value in values.items():
                setattr(self, key, value)

        @classmethod
        def load(
            cls,
            *,
            env: Mapping[str, str] | None = None,
            env_file: str | os.PathLike[str] | None = None,
            prefix: str | None = None,
            case_sensitive: bool = False,
            defaults: Mapping[str, str] | None = None,
        ) -> "FakeBaseSettings":
            env = env or {}
            prefix = prefix or ""
            data: dict[str, str] = {}
            for key, value in env.items():
                if prefix and key.startswith(prefix):
                    field = key[len(prefix) :]
                else:
                    field = key
                if not case_sensitive:
                    field = field.lower()
                if field in {"max_reconnect_attempts"}:
                    data[field] = int(value)
                elif field in {
                    "reconnect_time_wait",
                    "ping_interval",
                    "connect_timeout",
                    "drain_timeout",
                    "request_timeout",
                }:
                    data[field] = float(value)
                else:
                    data[field] = value
            if defaults:
                for key, value in defaults.items():
                    data.setdefault(key.lower(), value)
            return cls(**data)

    fake_module = types.ModuleType("env_settings")
    fake_module.BaseSettings = FakeBaseSettings  # type: ignore[attr-defined]
    fake_module.load_settings = lambda *args, **kwargs: FakeBaseSettings.load(*args, **kwargs)

    monkeypatch.setitem(sys.modules, "env_settings", fake_module)
    import service_toolkit.nats as nats_module

    importlib.reload(nats_module)

    raw_env = {
        "NATS_SERVERS": "nats://example:4222",
        "NATS_MAX_RECONNECT_ATTEMPTS": "7",
        "NATS_RECONNECT_TIME_WAIT": "3.5",
    }
    settings = nats_module.NATSSettings.from_env(raw_env)

    assert settings.servers == ("nats://example:4222",)
    assert settings.max_reconnect_attempts == 7
    assert settings.reconnect_time_wait == pytest.approx(3.5)

    monkeypatch.delitem(sys.modules, "env_settings", raising=False)
    importlib.reload(nats_module)


@pytest.mark.asyncio
async def test_ensure_stream_and_consumer(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = DummyConnection()

    async def fake_connect(**_: object) -> DummyConnection:
        return connection

    monkeypatch.setattr(nats_helpers, "connect", fake_connect)

    client = NATSClient(NATSSettings(jetstream_domain="events"))
    await client.ensure_stream("auth_stream", ["auth.user.*"])

    js = connection._jetstream
    assert js.add_stream_calls
    assert js.last_domain == "events"

    await client.ensure_consumer("auth_stream", "sync-core")
    assert js.add_consumer_calls

    await client.close()
