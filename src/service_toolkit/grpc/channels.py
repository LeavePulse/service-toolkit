"""Shared gRPC channel management — mirrors web/http.py pattern."""

from __future__ import annotations

import asyncio
import logging
from threading import Lock
from typing import Any, cast
from weakref import WeakKeyDictionary

import grpc

logger = logging.getLogger(__name__)

_CHANNEL_LOCK = Lock()
_SHARED_CHANNELS: dict[str, grpc.aio.Channel] = {}
_SHARED_CHANNEL_SPECS: dict[str, tuple[object, ...]] = {}
_GRPC_KEEPALIVE_TIME_MS = 300_000
_GRPC_KEEPALIVE_TIMEOUT_MS = 10_000
_GRPC_KEEPALIVE_PERMIT_WITHOUT_CALLS = 0
_GRPC_MAX_RECEIVE_MESSAGE_LENGTH = 16 * 1024 * 1024


class _MultiCallableProxy:
    """Resolve the real per-loop multi-callable lazily at invocation time."""

    def __init__(
        self,
        *,
        channel: _SharedChannelProxy,
        method_name: str,
        args: tuple[object, ...],
        kwargs: dict[str, object],
    ) -> None:
        self._channel = channel
        self._method_name = method_name
        self._args = args
        self._kwargs = kwargs

    def _resolve(self) -> Any:
        return self._channel._build_multi_callable(  # noqa: SLF001
            self._method_name,
            *self._args,
            **self._kwargs,
        )

    def __call__(self, *args: object, **kwargs: object) -> Any:
        return self._resolve()(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._resolve(), name)


class _SharedChannelProxy(grpc.aio.Channel):
    """Expose one logical gRPC channel while isolating real channels per loop."""

    def __init__(
        self,
        *,
        key: str,
        target: str,
        token: str | None,
        service_name: str,
        options: list[tuple[str, int]],
    ) -> None:
        self._key = key
        self._target = target
        self._token = token
        self._service_name = service_name
        self._options = tuple(options)
        self._lock = Lock()
        self._channels: WeakKeyDictionary[asyncio.AbstractEventLoop, grpc.aio.Channel] = (
            WeakKeyDictionary()
        )

    def _create_channel(self) -> grpc.aio.Channel:
        from .metrics import GrpcClientMetricsInterceptor

        interceptors: list[grpc.aio.ClientInterceptor] = [
            GrpcClientMetricsInterceptor(
                service_name=self._service_name,
                target=self._target,
            )
        ]
        if self._token is not None:
            from .interceptors import InternalTokenClientInterceptor

            interceptors.append(InternalTokenClientInterceptor(self._token))

        return grpc.aio.insecure_channel(
            self._target,
            options=list(self._options),
            interceptors=interceptors or None,
        )

    def _get_or_create_channel(self) -> grpc.aio.Channel:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError as exc:
            msg = (
                "Shared gRPC channels can only be used from a running event loop. "
                f"Channel key={self._key!r} target={self._target!r}."
            )
            raise RuntimeError(msg) from exc

        with self._lock:
            channel = self._channels.get(loop)
            if channel is not None:
                return channel

            channel = self._create_channel()
            self._channels[loop] = channel
            logger.info(
                "Created shared gRPC channel %r → %s on loop=%s",
                self._key,
                self._target,
                hex(id(loop)),
            )
            return channel

    def _build_multi_callable(self, method_name: str, *args: object, **kwargs: object) -> Any:
        channel = self._get_or_create_channel()
        builder = getattr(channel, method_name)
        return builder(*args, **kwargs)

    async def _close_channel(
        self,
        loop: asyncio.AbstractEventLoop,
        channel: grpc.aio.Channel,
        *,
        grace: float | None,
    ) -> None:
        current_loop: asyncio.AbstractEventLoop | None
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if current_loop is loop:
            await channel.close(grace)
            return

        if loop.is_running() and not loop.is_closed():
            future = asyncio.run_coroutine_threadsafe(channel.close(grace), loop)
            await asyncio.wrap_future(future)
            return

        logger.debug(
            "Skipping close for shared gRPC channel %r on inactive loop=%s",
            self._key,
            hex(id(loop)),
        )

    def unary_unary(
        self,
        method: str,
        request_serializer: Any = None,
        response_deserializer: Any = None,
        _registered_method: bool | None = False,
    ) -> grpc.aio.UnaryUnaryMultiCallable:
        return cast(
            "grpc.aio.UnaryUnaryMultiCallable",
            _MultiCallableProxy(
                channel=self,
                method_name="unary_unary",
                args=(method,),
                kwargs={
                    "request_serializer": request_serializer,
                    "response_deserializer": response_deserializer,
                    "_registered_method": _registered_method,
                },
            ),
        )

    def unary_stream(
        self,
        method: str,
        request_serializer: Any = None,
        response_deserializer: Any = None,
        _registered_method: bool | None = False,
    ) -> grpc.aio.UnaryStreamMultiCallable:
        return cast(
            "grpc.aio.UnaryStreamMultiCallable",
            _MultiCallableProxy(
                channel=self,
                method_name="unary_stream",
                args=(method,),
                kwargs={
                    "request_serializer": request_serializer,
                    "response_deserializer": response_deserializer,
                    "_registered_method": _registered_method,
                },
            ),
        )

    def stream_unary(
        self,
        method: str,
        request_serializer: Any = None,
        response_deserializer: Any = None,
        _registered_method: bool | None = False,
    ) -> grpc.aio.StreamUnaryMultiCallable:
        return cast(
            "grpc.aio.StreamUnaryMultiCallable",
            _MultiCallableProxy(
                channel=self,
                method_name="stream_unary",
                args=(method,),
                kwargs={
                    "request_serializer": request_serializer,
                    "response_deserializer": response_deserializer,
                    "_registered_method": _registered_method,
                },
            ),
        )

    def stream_stream(
        self,
        method: str,
        request_serializer: Any = None,
        response_deserializer: Any = None,
        _registered_method: bool | None = False,
    ) -> grpc.aio.StreamStreamMultiCallable:
        return cast(
            "grpc.aio.StreamStreamMultiCallable",
            _MultiCallableProxy(
                channel=self,
                method_name="stream_stream",
                args=(method,),
                kwargs={
                    "request_serializer": request_serializer,
                    "response_deserializer": response_deserializer,
                    "_registered_method": _registered_method,
                },
            ),
        )

    async def channel_ready(self) -> None:
        await self._get_or_create_channel().channel_ready()

    def get_state(self, try_to_connect: bool = False) -> grpc.ChannelConnectivity:
        return self._get_or_create_channel().get_state(try_to_connect=try_to_connect)

    async def wait_for_state_change(
        self,
        last_observed_state: grpc.ChannelConnectivity,
    ) -> None:
        await self._get_or_create_channel().wait_for_state_change(last_observed_state)

    async def close(self, grace: float | None = None) -> None:
        with self._lock:
            channels = list(self._channels.items())
            self._channels = WeakKeyDictionary()

        for loop, channel in channels:
            await self._close_channel(loop, channel, grace=grace)

    async def __aenter__(self) -> _SharedChannelProxy:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()


def build_shared_channel(
    *,
    key: str,
    target: str,
    token: str | None = None,
    service_name: str | None = None,
) -> grpc.aio.Channel:
    """Return a process-wide shared async gRPC channel.

    If *token* is provided, it is sent as ``x-internal-token`` metadata
    via a call-credentials interceptor attached to the channel.
    """
    normalized_key = str(key).strip()
    if not normalized_key:
        msg = "Shared gRPC channel key must not be empty."
        raise ValueError(msg)

    normalized_target = str(target).strip()
    if not normalized_target:
        msg = "gRPC target must not be empty."
        raise ValueError(msg)

    normalized_token = str(token or "").strip() or None
    normalized_service_name = str(service_name or "").strip() or normalized_key.split(
        ".", maxsplit=1
    )[0].replace("_", "-")
    spec = (normalized_target, normalized_token, normalized_service_name)

    with _CHANNEL_LOCK:
        existing = _SHARED_CHANNELS.get(normalized_key)
        existing_spec = _SHARED_CHANNEL_SPECS.get(normalized_key)
        if existing is not None:
            if existing_spec != spec:
                msg = (
                    "Shared gRPC channel key was reused with different "
                    f"configuration: {normalized_key}"
                )
                raise RuntimeError(msg)
            return existing

        options = [
            ("grpc.keepalive_time_ms", _GRPC_KEEPALIVE_TIME_MS),
            ("grpc.keepalive_timeout_ms", _GRPC_KEEPALIVE_TIMEOUT_MS),
            ("grpc.keepalive_permit_without_calls", _GRPC_KEEPALIVE_PERMIT_WITHOUT_CALLS),
            ("grpc.max_receive_message_length", _GRPC_MAX_RECEIVE_MESSAGE_LENGTH),
        ]
        channel = _SharedChannelProxy(
            key=normalized_key,
            target=normalized_target,
            token=normalized_token,
            service_name=normalized_service_name,
            options=options,
        )

        _SHARED_CHANNELS[normalized_key] = channel
        _SHARED_CHANNEL_SPECS[normalized_key] = spec
        logger.info(
            "Registered shared gRPC channel proxy %r → %s",
            normalized_key,
            normalized_target,
        )
        return channel


async def close_shared_channels(*keys: str) -> None:
    """Close one or more shared gRPC channels."""
    normalized_keys = [str(k).strip() for k in keys if str(k).strip()]
    with _CHANNEL_LOCK:
        if normalized_keys:
            channels = [
                (k, _SHARED_CHANNELS.pop(k, None)) for k in normalized_keys
            ]
            for k in normalized_keys:
                _SHARED_CHANNEL_SPECS.pop(k, None)
        else:
            channels = list(_SHARED_CHANNELS.items())
            _SHARED_CHANNELS.clear()
            _SHARED_CHANNEL_SPECS.clear()

    for key, channel in channels:
        if channel is not None:
            await channel.close()
            logger.info("Closed shared gRPC channel %r", key)


__all__ = ["build_shared_channel", "close_shared_channels"]
