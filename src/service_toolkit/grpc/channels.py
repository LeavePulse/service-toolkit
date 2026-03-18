"""Shared gRPC channel management — mirrors web/http.py pattern."""

from __future__ import annotations

import logging
from threading import Lock

import grpc

logger = logging.getLogger(__name__)

_CHANNEL_LOCK = Lock()
_SHARED_CHANNELS: dict[str, grpc.aio.Channel] = {}
_SHARED_CHANNEL_SPECS: dict[str, tuple[object, ...]] = {}


def build_shared_channel(
    *,
    key: str,
    target: str,
    token: str | None = None,
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
    spec = (normalized_target, normalized_token)

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
            ("grpc.keepalive_time_ms", 30_000),
            ("grpc.keepalive_timeout_ms", 10_000),
            ("grpc.keepalive_permit_without_calls", 1),
            ("grpc.max_receive_message_length", 16 * 1024 * 1024),
        ]

        interceptors: list[grpc.aio.ClientInterceptor] = []
        if normalized_token is not None:
            from .interceptors import InternalTokenClientInterceptor

            interceptors.append(InternalTokenClientInterceptor(normalized_token))

        channel = grpc.aio.insecure_channel(
            normalized_target,
            options=options,
            interceptors=interceptors or None,
        )

        _SHARED_CHANNELS[normalized_key] = channel
        _SHARED_CHANNEL_SPECS[normalized_key] = spec
        logger.info("Created shared gRPC channel %r → %s", normalized_key, normalized_target)
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
