"""JWT forwarding across the gRPC mesh.

Used together by:

- **Caller side** (platform-api or another upstream): set ``forwarded_jwt`` via
  :func:`set_forwarded_jwt` for the duration of one request handling. The
  client interceptor copies the token into ``authorization`` gRPC metadata
  for every outbound call originating in that task.

- **Callee side** (internal service hosting servicers): install
  :class:`JwtContextServerInterceptor` and pass an
  :class:`auth_service_sdk.JWTVerifier` instance. For each incoming call the
  interceptor reads the ``authorization`` header, verifies the JWT, and
  exposes the decoded :class:`auth_service_sdk.JWTPayload` via
  :func:`current_jwt_payload` for the lifetime of that RPC.

This is what lets a servicer reuse the **same** permission helpers the HTTP
layer used (``require_server_links_edit`` etc.) without inventing a new
authorization shape: the helpers still receive a ``JWTPayload``, only the
transport that carries it changed.

The forwarding-only path is intentionally separate from the existing
``x-internal-token`` interceptors: that token authenticates the *service*
caller, while this JWT authenticates the *user* on whose behalf the call
is made. Both are validated independently.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING, Any

import grpc

if TYPE_CHECKING:
    from auth_service_sdk import JWTPayload, JWTVerifier

logger = logging.getLogger(__name__)

_FORWARDED_JWT: ContextVar[str | None] = ContextVar(
    "leavepulse_forwarded_jwt", default=None
)
_CURRENT_JWT_PAYLOAD: ContextVar[JWTPayload | None] = ContextVar(
    "leavepulse_current_jwt_payload", default=None
)

_AUTHORIZATION_HEADER = "authorization"
_BEARER_PREFIX = "bearer "


def set_forwarded_jwt(jwt: str | None) -> Token[str | None]:
    """Bind a JWT to the current task; returns a Token for ``reset_forwarded_jwt``."""
    return _FORWARDED_JWT.set(jwt or None)


def reset_forwarded_jwt(token: Token[str | None]) -> None:
    """Restore the previous ``forwarded_jwt`` value."""
    _FORWARDED_JWT.reset(token)


def forwarded_jwt() -> str | None:
    """Return the JWT currently bound for outbound forwarding, if any."""
    return _FORWARDED_JWT.get()


def current_jwt_payload() -> JWTPayload | None:
    """Return the decoded JWT payload of the current inbound RPC, if any."""
    return _CURRENT_JWT_PAYLOAD.get()


class JwtForwardingClientInterceptor(grpc.aio.UnaryUnaryClientInterceptor):
    """Copy the task-local ``forwarded_jwt`` into outbound metadata.

    Adds an ``authorization: Bearer <jwt>`` header when, and only when, a
    JWT is bound via :func:`set_forwarded_jwt`. Public (unauthenticated)
    calls remain unauthenticated — no header is added when no JWT is set.
    """

    async def intercept_unary_unary(
        self,
        continuation: Callable[..., Awaitable[Any]],
        client_call_details: grpc.aio.ClientCallDetails,
        request: Any,
    ) -> Any:
        jwt = forwarded_jwt()
        if not jwt:
            return await continuation(client_call_details, request)

        metadata = _normalize_metadata(client_call_details.metadata)
        if any(
            str(key).lower() == _AUTHORIZATION_HEADER for key, _ in metadata
        ):
            # Caller already supplied an explicit token; do not override.
            return await continuation(client_call_details, request)

        metadata.append((_AUTHORIZATION_HEADER, f"Bearer {jwt}"))
        updated_details = grpc.aio.ClientCallDetails(
            method=client_call_details.method,
            timeout=client_call_details.timeout,
            metadata=metadata,
            credentials=client_call_details.credentials,
            wait_for_ready=client_call_details.wait_for_ready,
        )
        return await continuation(updated_details, request)


class JwtContextServerInterceptor(grpc.aio.ServerInterceptor):
    """Decode the inbound ``authorization`` JWT and expose it via contextvar.

    The decoded :class:`JWTPayload` is bound to ``current_jwt_payload`` for
    the duration of the wrapped handler. Servicers retrieve it with
    :func:`current_jwt_payload` and pass it into the existing
    permission helpers — there is no shape conversion at the seam.

    Validation failures terminate the RPC with ``UNAUTHENTICATED``. Calls
    that don't carry an ``authorization`` header pass through unchanged
    (anonymous access) — service-level enforcement is the caller's job
    via the existing ``InternalTokenInterceptor``.
    """

    def __init__(self, jwt_verifier: JWTVerifier[Any]) -> None:
        self._verifier = jwt_verifier

    async def intercept_service(
        self,
        continuation: Callable[..., Awaitable[Any]],
        handler_call_details: grpc.HandlerCallDetails,
    ) -> Any:
        method = handler_call_details.method or ""
        if "grpc.health" in method or "grpc.reflection" in method:
            return await continuation(handler_call_details)

        handler = await continuation(handler_call_details)
        if handler is None:
            return None

        verifier = self._verifier
        return _wrap_handler_with_jwt_context(handler, verifier)


def _wrap_handler_with_jwt_context(
    handler: grpc.RpcMethodHandler,
    verifier: JWTVerifier[Any],
) -> grpc.RpcMethodHandler:
    if not handler.unary_unary:
        # Streaming RPCs are not yet used by the BFF; passthrough.
        return handler

    inner_unary_unary = handler.unary_unary

    async def _wrapped(request: Any, context: grpc.aio.ServicerContext) -> Any:
        payload = await _verify_metadata_jwt(context, verifier)
        token: Token[JWTPayload | None] | None = None
        if payload is not None:
            token = _CURRENT_JWT_PAYLOAD.set(payload)
        try:
            return await inner_unary_unary(request, context)
        finally:
            if token is not None:
                _CURRENT_JWT_PAYLOAD.reset(token)

    return grpc.unary_unary_rpc_method_handler(
        _wrapped,
        request_deserializer=handler.request_deserializer,
        response_serializer=handler.response_serializer,
    )


async def _verify_metadata_jwt(
    context: grpc.aio.ServicerContext,
    verifier: JWTVerifier[Any],
) -> JWTPayload | None:
    auth_value = _read_authorization(context.invocation_metadata())
    if not auth_value:
        return None

    if not auth_value.lower().startswith(_BEARER_PREFIX):
        await context.abort(
            grpc.StatusCode.UNAUTHENTICATED,
            "authorization metadata must be a Bearer token",
        )
        return None  # unreachable — abort raises

    token = auth_value[len(_BEARER_PREFIX):].strip()
    try:
        payload = await verifier.verify(token)
    except Exception as exc:  # noqa: BLE001 — verifier-specific errors aren't typed
        logger.warning("gRPC inbound JWT failed verification: %s", exc)
        await context.abort(
            grpc.StatusCode.UNAUTHENTICATED,
            "invalid or expired authorization JWT",
        )
        return None  # unreachable — abort raises
    return payload


def _read_authorization(
    metadata: Sequence[tuple[str, str | bytes]] | None,
) -> str | None:
    if metadata is None:
        return None
    for key, value in metadata:
        if str(key).lower() == _AUTHORIZATION_HEADER:
            return value.decode("utf-8") if isinstance(value, bytes) else str(value)
    return None


def _normalize_metadata(
    metadata: Sequence[tuple[str, str]] | None,
) -> list[tuple[str, str]]:
    if metadata is None:
        return []
    return [(str(key), str(value)) for key, value in metadata]


__all__ = [
    "JwtContextServerInterceptor",
    "JwtForwardingClientInterceptor",
    "current_jwt_payload",
    "forwarded_jwt",
    "reset_forwarded_jwt",
    "set_forwarded_jwt",
]
