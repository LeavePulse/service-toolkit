"""gRPC server interceptors for internal authentication."""

from __future__ import annotations

import logging
from typing import Any

import grpc

logger = logging.getLogger(__name__)


class InternalTokenInterceptor(grpc.aio.ServerInterceptor):
    """Validate ``x-internal-token`` from gRPC metadata.

    Replaces the HTTP ``require_internal_token()`` guard used by
    internal Litestar controllers.
    """

    def __init__(self, token: str) -> None:
        if not token:
            msg = "Internal token must not be empty."
            raise ValueError(msg)
        self._token = token

    async def intercept_service(
        self,
        continuation: Any,
        handler_call_details: grpc.HandlerCallDetails,
    ) -> Any:
        # Allow health checks without auth
        method = handler_call_details.method or ""
        if "grpc.health" in method or "grpc.reflection" in method:
            return await continuation(handler_call_details)

        metadata = dict(handler_call_details.invocation_metadata or [])
        received_token = metadata.get("x-internal-token")

        if received_token != self._token:
            logger.warning(
                "gRPC auth failed for %s — invalid or missing internal token",
                method,
            )

            async def _abort(
                request: Any, context: grpc.aio.ServicerContext,
            ) -> None:
                await context.abort(
                    grpc.StatusCode.UNAUTHENTICATED,
                    "Invalid or missing internal token",
                )

            return grpc.unary_unary_rpc_method_handler(_abort)

        return await continuation(handler_call_details)


class InternalTokenCallCredentials:
    """Client-side metadata injector for ``x-internal-token``."""

    def __init__(self, token: str) -> None:
        self._token = token

    def metadata(self) -> list[tuple[str, str]]:
        return [("x-internal-token", self._token)]


__all__ = ["InternalTokenCallCredentials", "InternalTokenInterceptor"]
