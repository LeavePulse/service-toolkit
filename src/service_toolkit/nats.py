"""NATS helper utilities."""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Sequence

try:  # pragma: no cover - import guard
    from nats import connect
    from nats.aio.client import Client as _NATS
    from nats.aio.msg import Msg
    from nats.aio.subscription import Subscription
    from nats.js.api import ConsumerConfig, ConsumerInfo, StreamConfig, StreamInfo
    from nats.js.client import JetStreamContext
    from nats.js.errors import NotFoundError
except ModuleNotFoundError as exc:  # pragma: no cover - runtime guard
    if exc.name == "nats":
        raise ModuleNotFoundError(
            "NATS helpers require the optional 'nats' extra. "
            "Install with 'pip install service-toolkit[nats]'."
        ) from exc
    raise


DEFAULT_NATS_URL = "nats://127.0.0.1:4222"

MessageCallback = Callable[[Msg], Awaitable[None]]


def _parse_servers(value: str | None) -> tuple[str, ...]:
    if not value:
        return (DEFAULT_NATS_URL,)
    servers = tuple(
        item.strip()
        for item in re.split(r"[;,\s]+", value)
        if item.strip()
    )
    if servers:
        return servers
    return (DEFAULT_NATS_URL,)


def _normalize_servers(value: str | Sequence[str] | None) -> tuple[str, ...]:
    if value is None:
        return (DEFAULT_NATS_URL,)
    if isinstance(value, str):
        return _parse_servers(value)
    combined = " ".join(str(item) for item in value)
    return _parse_servers(combined)


def _parse_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:  # pragma: no cover - defensive
        raise ValueError(f"Invalid integer value: {value!r}") from exc


def _parse_float(value: str | None, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except ValueError as exc:  # pragma: no cover - defensive
        raise ValueError(f"Invalid float value: {value!r}") from exc


@dataclass(slots=True)
class NATSSettings:
    """Configuration parameters for NATS connectivity."""

    servers: tuple[str, ...] = (DEFAULT_NATS_URL,)
    name: str | None = None
    user: str | None = None
    password: str | None = None
    token: str | None = None
    max_reconnect_attempts: int = 60
    reconnect_time_wait: float = 2.0
    ping_interval: float = 60.0
    connect_timeout: float = 2.0
    drain_timeout: float = 5.0
    request_timeout: float = 2.0
    jetstream_domain: str | None = None

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        prefix: str = "NATS_",
        env_file: str | os.PathLike[str] | None = None,
        case_sensitive: bool = False,
    ) -> "NATSSettings":
        """Create settings from environment variables.

        Prefers :mod:`env_settings` when available to keep configuration consistent
        with other services, but falls back to simple environment parsing and still
        allows manual instantiation of :class:`NATSSettings`.
        """

        try:
            from env_settings import BaseSettings as _BaseSettings  # type: ignore
        except ModuleNotFoundError:  # pragma: no cover - optional dependency
            _BaseSettings = None

        if _BaseSettings is not None:

            class _NATSConfig(_BaseSettings):  # type: ignore[misc]
                servers: str | list[str] | None = None
                name: str | None = None
                user: str | None = None
                password: str | None = None
                token: str | None = None
                max_reconnect_attempts: int = 60
                reconnect_time_wait: float = 2.0
                ping_interval: float = 60.0
                connect_timeout: float = 2.0
                drain_timeout: float = 5.0
                request_timeout: float = 2.0
                jetstream_domain: str | None = None

            loaded = _NATSConfig.load(
                env=env,
                env_file=env_file,
                prefix=prefix,
                case_sensitive=case_sensitive,
            )

            return cls(
                servers=_normalize_servers(loaded.servers),
                name=loaded.name,
                user=loaded.user,
                password=loaded.password,
                token=loaded.token,
                max_reconnect_attempts=loaded.max_reconnect_attempts,
                reconnect_time_wait=loaded.reconnect_time_wait,
                ping_interval=loaded.ping_interval,
                connect_timeout=loaded.connect_timeout,
                drain_timeout=loaded.drain_timeout,
                request_timeout=loaded.request_timeout,
                jetstream_domain=loaded.jetstream_domain,
            )

        source = env if env is not None else os.environ
        servers = _parse_servers(source.get(f"{prefix}SERVERS"))
        name = source.get(f"{prefix}NAME")
        user = source.get(f"{prefix}USER")
        password = source.get(f"{prefix}PASSWORD")
        token = source.get(f"{prefix}TOKEN")
        max_reconnect_attempts = _parse_int(
            source.get(f"{prefix}MAX_RECONNECT_ATTEMPTS"), cls.max_reconnect_attempts
        )
        reconnect_time_wait = _parse_float(
            source.get(f"{prefix}RECONNECT_TIME_WAIT"), cls.reconnect_time_wait
        )
        ping_interval = _parse_float(
            source.get(f"{prefix}PING_INTERVAL"), cls.ping_interval
        )
        connect_timeout = _parse_float(
            source.get(f"{prefix}CONNECT_TIMEOUT"), cls.connect_timeout
        )
        drain_timeout = _parse_float(
            source.get(f"{prefix}DRAIN_TIMEOUT"), cls.drain_timeout
        )
        request_timeout = _parse_float(
            source.get(f"{prefix}REQUEST_TIMEOUT"), cls.request_timeout
        )
        jetstream_domain = source.get(f"{prefix}JETSTREAM_DOMAIN")

        return cls(
            servers=servers,
            name=name,
            user=user,
            password=password,
            token=token,
            max_reconnect_attempts=max_reconnect_attempts,
            reconnect_time_wait=reconnect_time_wait,
            ping_interval=ping_interval,
            connect_timeout=connect_timeout,
            drain_timeout=drain_timeout,
            request_timeout=request_timeout,
            jetstream_domain=jetstream_domain,
        )

    def connection_options(self) -> dict[str, Any]:
        """Return options suitable for :func:`nats.connect`."""

        options: dict[str, Any] = {
            "servers": list(self.servers),
            "max_reconnect_attempts": self.max_reconnect_attempts,
            "reconnect_time_wait": self.reconnect_time_wait,
            "ping_interval": self.ping_interval,
            "connect_timeout": self.connect_timeout,
        }
        if self.name:
            options["name"] = self.name
        if self.user and self.password:
            options["user"] = self.user
            options["password"] = self.password
        if self.token:
            options["token"] = self.token
        return options


class NATSClient:
    """High-level asynchronous NATS client wrapper."""

    def __init__(self, settings: NATSSettings) -> None:
        self.settings = settings
        self._connection: _NATS | None = None
        self._jetstream: JetStreamContext | None = None
        self._lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self._connection is not None and self._connection.is_connected

    async def connect(self) -> _NATS:
        """Establish a NATS connection if not already connected."""

        if self._connection is not None and self._connection.is_connected:
            return self._connection

        async with self._lock:
            if self._connection is not None and self._connection.is_connected:
                return self._connection

            self._connection = await connect(**self.settings.connection_options())
            self._jetstream = None
            return self._connection

    async def jetstream(self) -> JetStreamContext:
        """Return a JetStream context bound to the connection."""

        if self._jetstream is not None:
            return self._jetstream

        connection = await self.connect()
        self._jetstream = connection.jetstream(domain=self.settings.jetstream_domain)
        return self._jetstream

    async def publish(
        self,
        subject: str,
        payload: bytes | bytearray | memoryview,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        """Publish raw bytes to a subject."""

        connection = await self.connect()
        await connection.publish(subject, bytes(payload), headers=dict(headers or {}))

    async def publish_json(
        self,
        subject: str,
        payload: Any,
        *,
        headers: Mapping[str, str] | None = None,
        dumps: Callable[[Any], str] = json.dumps,
    ) -> None:
        """Publish JSON payload encoded as UTF-8."""

        data = dumps(payload).encode("utf-8")
        await self.publish(subject, data, headers=headers)

    async def request(
        self,
        subject: str,
        payload: bytes | bytearray | memoryview,
        *,
        timeout: float | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Msg:
        """Send a request and await a reply."""

        connection = await self.connect()
        reply = await connection.request(
            subject,
            bytes(payload),
            timeout=timeout or self.settings.request_timeout,
            headers=dict(headers or {}),
        )
        return reply

    async def request_json(
        self,
        subject: str,
        payload: Any,
        *,
        timeout: float | None = None,
        headers: Mapping[str, str] | None = None,
        dumps: Callable[[Any], str] = json.dumps,
    ) -> Msg:
        """Send a JSON request and await a reply."""

        data = dumps(payload).encode("utf-8")
        return await self.request(subject, data, timeout=timeout, headers=headers)

    async def subscribe(
        self,
        subject: str,
        *,
        queue: str | None = None,
        callback: MessageCallback,
        max_messages: int | None = None,
    ) -> Subscription:
        """Subscribe to a subject with a callback."""

        connection = await self.connect()
        return await connection.subscribe(
            subject,
            queue=queue,
            cb=callback,
            max_msgs=max_messages,
        )

    async def ensure_stream(
        self,
        name: str,
        subjects: Sequence[str],
        *,
        config: StreamConfig | None = None,
    ) -> StreamInfo:
        """Ensure that a JetStream stream exists."""

        js = await self.jetstream()
        try:
            return await js.stream_info(name)
        except NotFoundError:
            if config is None:
                config = StreamConfig(name=name, subjects=list(subjects))
            else:
                if not getattr(config, "name", None):
                    config.name = name
                if not getattr(config, "subjects", None) and subjects:
                    config.subjects = list(subjects)
            return await js.add_stream(config)

    async def ensure_consumer(
        self,
        stream: str,
        durable_name: str,
        *,
        config: ConsumerConfig | None = None,
    ) -> ConsumerInfo:
        """Ensure that a durable consumer exists for a stream."""

        js = await self.jetstream()
        try:
            return await js.consumer_info(stream, durable_name)
        except NotFoundError:
            if config is None:
                config = ConsumerConfig(durable_name=durable_name)
            elif not getattr(config, "durable_name", None):
                config.durable_name = durable_name
            return await js.add_consumer(stream, config)

    async def close(self) -> None:
        """Drain and close the underlying connection."""

        if self._connection is None:
            return

        try:
            await asyncio.wait_for(self._connection.drain(), timeout=self.settings.drain_timeout)
        except asyncio.TimeoutError:
            await self._connection.close()
        finally:
            self._connection = None
            self._jetstream = None

    async def __aenter__(self) -> "NATSClient":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[override]
        await self.close()


__all__ = ["NATSClient", "NATSSettings", "DEFAULT_NATS_URL"]
