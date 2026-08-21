"""gRPC interceptors for internal authentication."""

from __future__ import annotations

import hmac
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import grpc

logger = logging.getLogger(__name__)

#: Checks a token the shared secret did not match. Called with the RPC's method
#: name and the presented token; returns a callable that wraps the handler when
#: the caller is admitted (so the verifier can bind whatever it proved for the
#: handler's duration), or ``None`` to leave the call refused.
#:
#: Async because deciding may need I/O — a per-host credential lives in a table,
#: not in config.
AlternateTokenVerifier = Callable[
    [str, str],
    Awaitable[Callable[[Any], Any] | None],
]


class InternalTokenInterceptor(grpc.aio.ServerInterceptor):
    """Validate ``x-internal-token`` from gRPC metadata.

    Replaces the HTTP ``require_internal_token()`` guard used by
    internal Litestar controllers.
    """

    def __init__(
        self,
        token: str,
        exempt_methods: Sequence[str] = (),
        alternate_verifier: AlternateTokenVerifier | None = None,
    ) -> None:
        if not token:
            msg = "Internal token must not be empty."
            raise ValueError(msg)
        self._token = token
        # Fully-qualified method names (e.g.
        # "/leavepulse.control.v1.AgentGateway/Enroll") served WITHOUT the
        # internal token — for genuine first-contact RPCs that bootstrap the
        # token itself. Matched by suffix so the leading slash/package is
        # optional ("AgentGateway/Enroll" also matches).
        self._exempt_methods = tuple(exempt_methods)
        # A second credential this service accepts on the same header, checked
        # only after the shared token fails to match. Lets a service admit
        # callers it authenticates its own way — the control-plane's per-host
        # agent tokens — without every service having to know about them, and
        # without weakening the default: no verifier means shared-token-only,
        # exactly as before.
        self._alternate_verifier = alternate_verifier

    async def intercept_service(
        self,
        continuation: Any,
        handler_call_details: grpc.HandlerCallDetails,
    ) -> Any:
        # Allow health checks without auth
        method = handler_call_details.method or ""
        if "grpc.health" in method or "grpc.reflection" in method:
            return await continuation(handler_call_details)
        if any(method.endswith(exempt) for exempt in self._exempt_methods):
            return await continuation(handler_call_details)

        metadata = dict(handler_call_details.invocation_metadata or [])
        received_token = metadata.get("x-internal-token")

        if not hmac.compare_digest(received_token or "", self._token):
            if self._alternate_verifier is not None and received_token:
                admitted = await self._alternate_verifier(method, received_token)
                if admitted is not None:
                    return admitted(await continuation(handler_call_details))
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


class InternalTokenClientInterceptor(grpc.aio.UnaryUnaryClientInterceptor):
    """Inject ``x-internal-token`` into unary-unary client metadata."""

    def __init__(self, token: str) -> None:
        if not token:
            msg = "Internal token must not be empty."
            raise ValueError(msg)
        self._token = token

    async def intercept_unary_unary(
        self,
        continuation: Any,
        client_call_details: grpc.aio.ClientCallDetails,
        request: Any,
    ) -> Any:
        metadata = list(_normalize_metadata(client_call_details.metadata))
        metadata.append(("x-internal-token", self._token))
        updated_details = grpc.aio.ClientCallDetails(
            method=client_call_details.method,
            timeout=client_call_details.timeout,
            metadata=metadata,
            credentials=client_call_details.credentials,
            wait_for_ready=client_call_details.wait_for_ready,
        )
        return await continuation(updated_details, request)


def _normalize_metadata(
    metadata: Sequence[tuple[str, str]] | None,
) -> list[tuple[str, str]]:
    if metadata is None:
        return []
    return [(str(key), str(value)) for key, value in metadata]


__all__ = [
    "InternalTokenCallCredentials",
    "InternalTokenClientInterceptor",
    "InternalTokenInterceptor",
]
