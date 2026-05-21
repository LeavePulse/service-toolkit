"""Small client facade for gRPC upstreams."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, TypeVar

import grpc

from .calls import grpc_call
from .channels import build_shared_channel, close_shared_channels

_StubT = TypeVar("_StubT")


class GrpcClient:
    """Own one shared channel key plus a default unary-call timeout."""

    __slots__ = (
        "_channel",
        "_key",
        "_target",
        "_timeout_seconds",
        "_token",
    )

    def __init__(
        self,
        *,
        key: str,
        target: str,
        timeout_seconds: float,
        token: str | None = None,
        service_name: str | None = None,
    ) -> None:
        timeout = float(timeout_seconds)
        if timeout <= 0:
            msg = "gRPC timeout_seconds must be positive."
            raise ValueError(msg)

        self._key = str(key).strip()
        self._target = str(target).strip()
        self._token = str(token or "").strip() or None
        self._timeout_seconds = timeout
        self._channel = build_shared_channel(
            key=self._key,
            target=self._target,
            token=self._token,
            service_name=service_name,
        )

    @property
    def key(self) -> str:
        return self._key

    @property
    def channel(self) -> grpc.aio.Channel:
        return self._channel

    @property
    def timeout_seconds(self) -> float:
        return self._timeout_seconds

    def stub(self, factory: Callable[[grpc.aio.Channel], _StubT]) -> _StubT:
        return factory(self._channel)

    async def call(
        self,
        method: Any,
        request: Any,
        *,
        timeout: float | None = None,
        resource: str | None = None,
        resource_id: object = None,
        extra_errors: Mapping[grpc.StatusCode, Any] | None = None,
    ) -> Any:
        return await grpc_call(
            method,
            request,
            timeout=self._timeout_seconds if timeout is None else float(timeout),
            resource=resource,
            resource_id=resource_id,
            extra_errors=extra_errors,
        )

    async def close(self) -> None:
        await close_shared_channels(self._key)


def build_grpc_client(
    *,
    key: str,
    target: str,
    timeout_seconds: float,
    token: str | None = None,
    service_name: str | None = None,
) -> GrpcClient:
    return GrpcClient(
        key=key,
        target=target,
        timeout_seconds=timeout_seconds,
        token=token,
        service_name=service_name,
    )


__all__ = ["GrpcClient", "build_grpc_client"]
