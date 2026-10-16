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

- **Verified bearer handoff** (opt-in on both ends): a service that must call
  one upstream *as the end user* (verification-service calling auth-service)
  builds its server with ``retain_verified_bearer=True`` (or wraps its own
  verified HTTP auth in :func:`verified_bearer_scope`) and builds exactly that
  one client with ``verified_bearer_handoff=True``. The raw token is retained
  only after the inbound verifier accepted it and only for the RPC or request
  that verified it; every channel that did not opt in sends nothing from it.
  This is deliberately not generic propagation: an inbound bearer is a bearer
  secret, and the default for every server and channel is to drop it.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING, Any

import grpc

if TYPE_CHECKING:
    from auth_service_sdk import JWTPayload, JWTVerifier

logger = logging.getLogger(__name__)

_FORWARDED_JWT: ContextVar[str | None] = ContextVar(
    "leavepulse_forwarded_jwt", default=None
)
_FORWARDED_ON_BEHALF: ContextVar[int | None] = ContextVar(
    "leavepulse_forwarded_on_behalf", default=None
)
_CURRENT_JWT_PAYLOAD: ContextVar[JWTPayload | None] = ContextVar(
    "leavepulse_current_jwt_payload", default=None
)
_CURRENT_ON_BEHALF: ContextVar[int | None] = ContextVar(
    "leavepulse_current_on_behalf", default=None
)

_VERIFIED_BEARER: ContextVar[VerifiedBearer | None] = ContextVar(
    "leavepulse_verified_bearer", default=None
)

_AUTHORIZATION_HEADER = "authorization"
_ON_BEHALF_METADATA = "x-on-behalf-user-id"
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


def set_forwarded_on_behalf(user_id: int | None) -> Token[int | None]:
    """Bind an on-behalf user id forwarded alongside a bot's JWT.

    When a bot account acts on behalf of a resolved human, the bot's own JWT is
    still forwarded for authentication, but this user id rides in parallel
    metadata so callee servicers can treat the human as the effective subject.
    """
    return _FORWARDED_ON_BEHALF.set(user_id)


def reset_forwarded_on_behalf(token: Token[int | None]) -> None:
    """Restore the previous forwarded on-behalf user id."""
    _FORWARDED_ON_BEHALF.reset(token)


def forwarded_on_behalf() -> int | None:
    """Return the on-behalf user id bound for outbound forwarding, if any."""
    return _FORWARDED_ON_BEHALF.get()


def current_on_behalf() -> int | None:
    """Return the on-behalf user id of the current inbound RPC, if any."""
    return _CURRENT_ON_BEHALF.get()


def effective_user_id(payload: JWTPayload | None) -> int:
    """Return the subject the current RPC acts as.

    For a normal caller this is the JWT ``sub``. For a bot acting on behalf of a
    resolved human (``payload.is_bot`` and an inbound on-behalf id present), the
    human is the effective subject. Non-bot callers can never be impersonated:
    an on-behalf id is ignored unless the authenticated payload is a bot.
    """
    on_behalf = _CURRENT_ON_BEHALF.get()
    if on_behalf is not None and bool(getattr(payload, "is_bot", False)):
        return on_behalf
    return user_id_from_payload(payload)


def current_jwt_payload() -> JWTPayload | None:
    """Return the decoded JWT payload of the current inbound RPC, if any."""
    return _CURRENT_JWT_PAYLOAD.get()


def user_id_from_payload(payload: JWTPayload | None) -> int:
    """Extract the integer user id (``sub``) from a JWT payload.

    Raises ``AuthRequiredError`` when the payload is missing or its ``sub`` is
    not a usable integer — the shape every gRPC servicer was re-implementing.
    """
    from awesome_errors import AuthRequiredError  # type: ignore[import-not-found]

    try:
        return int(payload.sub)  # type: ignore[union-attr]
    except (AttributeError, TypeError, ValueError) as exc:
        raise AuthRequiredError("Authentication required.") from exc


#: RFC 6750 ``b64token``: what may follow ``Bearer `` in an authorization value.
_B64TOKEN = re.compile(r"[A-Za-z0-9\-._~+/]+=*")


class VerifiedBearer:
    """A raw user JWT that an inbound verifier accepted, redacted everywhere.

    The value is reachable only through :meth:`_reveal`, which this module's
    handoff interceptor calls; ``repr``/``str`` never show it and it refuses
    to be pickled, so it cannot reach a log line, an exception text or a
    serialized payload by accident.
    """

    __slots__ = ("__value",)

    def __init__(self, value: str) -> None:
        if not _B64TOKEN.fullmatch(value):
            # Never echo the value: it is a credential even when malformed.
            msg = "a verified bearer must be a single RFC 6750 b64token"
            raise ValueError(msg)
        self.__value = value

    def _reveal(self) -> str:
        return self.__value

    def __repr__(self) -> str:
        return "VerifiedBearer(<redacted>)"

    __str__ = __repr__

    def __reduce__(self) -> Any:
        msg = "a verified bearer cannot be serialized"
        raise TypeError(msg)


def verified_bearer_retained() -> bool:
    """Whether a verified bearer is retained for the current RPC or request.

    Diagnostic only: the token itself is never returned.
    """
    return _VERIFIED_BEARER.get() is not None


@contextmanager
def verified_bearer_scope(token: str) -> Iterator[None]:
    """Retain a *verified* user JWT for one HTTP request (the HTTP-side opt-in).

    Call it from the service's own auth middleware **after** its JWT verifier
    accepted *token* (signature, ``typ``, ``iss``, ``aud`` and expiry, as RFC
    8725 sections 3.1, 3.9 and 3.11 require:
    https://www.rfc-editor.org/rfc/rfc8725.html), around the downstream
    handling of that request only::

        payload = await verifier.verify(raw)  # raises on anything it rejects
        with verified_bearer_scope(raw):
            return await handler(request)

    Never pass a token the verifier did not accept. Only a channel built with
    ``verified_bearer_handoff=True`` sends it; the receiving service verifies
    it again. The previous value is restored when the block exits, also when
    it raises. A malformed token is refused without echoing it.
    """
    held = VerifiedBearer(str(token).strip())
    reset = _VERIFIED_BEARER.set(held)
    try:
        yield
    finally:
        _VERIFIED_BEARER.reset(reset)


class VerifiedBearerHandoffClientInterceptor(grpc.aio.UnaryUnaryClientInterceptor):
    """Send the retained verified bearer, on the one channel that opted in.

    Installed only by ``build_shared_channel(..., verified_bearer_handoff=True)``
    (or by hand on a single channel); every other channel never reads the
    retained bearer. It adds ``authorization: Bearer <jwt>`` to a fresh
    metadata list (never the caller's own) when a bearer is retained and the
    call carries no ``authorization`` yet; otherwise the call is untouched.
    Unary-unary only: a streaming call on the channel sends nothing.

    This only carries a token the inbound verifier accepted (RFC 8725 section
    3.11 and 3.9: its ``typ`` and ``aud`` were checked there) to the receiving
    service, which verifies it again with its own verifier; nothing here
    weakens that check or interacts with the caller policy.
    """

    async def intercept_unary_unary(
        self,
        continuation: Callable[..., Awaitable[Any]],
        client_call_details: grpc.aio.ClientCallDetails,
        request: Any,
    ) -> Any:
        held = _VERIFIED_BEARER.get()
        if held is None:
            return await continuation(client_call_details, request)
        metadata = _normalize_metadata(client_call_details.metadata)
        if any(str(key).lower() == _AUTHORIZATION_HEADER for key, _ in metadata):
            return await continuation(client_call_details, request)
        metadata.append((_AUTHORIZATION_HEADER, f"Bearer {held._reveal()}"))  # noqa: SLF001
        updated_details = grpc.aio.ClientCallDetails(
            method=client_call_details.method,
            timeout=client_call_details.timeout,
            metadata=metadata,
            credentials=client_call_details.credentials,
            wait_for_ready=client_call_details.wait_for_ready,
        )
        return await continuation(updated_details, request)


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
        on_behalf = forwarded_on_behalf()
        if on_behalf is not None and not any(
            str(key).lower() == _ON_BEHALF_METADATA for key, _ in metadata
        ):
            metadata.append((_ON_BEHALF_METADATA, str(on_behalf)))
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

    *retain_verified_bearer* (default ``False``) additionally keeps the raw
    token of a call whose JWT the verifier **accepted**, for that call only
    (reset when the handler returns or raises), so the one client built with
    ``verified_bearer_handoff=True`` can send it on. A rejected or absent
    token is never retained. See :func:`verified_bearer_scope` for the RFC
    8725 reasoning; streaming RPCs are passed through and retain nothing.
    """

    def __init__(
        self,
        jwt_verifier: JWTVerifier[Any],
        *,
        retain_verified_bearer: bool = False,
    ) -> None:
        self._verifier = jwt_verifier
        self._retain_verified_bearer = bool(retain_verified_bearer)

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
        return _wrap_handler_with_jwt_context(
            handler, verifier, retain_bearer=self._retain_verified_bearer
        )


def _wrap_handler_with_jwt_context(
    handler: grpc.RpcMethodHandler,
    verifier: JWTVerifier[Any],
    *,
    retain_bearer: bool = False,
) -> grpc.RpcMethodHandler:
    if not handler.unary_unary:
        # Streaming RPCs are not yet used by the BFF; passthrough.
        return handler

    inner_unary_unary = handler.unary_unary

    async def _wrapped(request: Any, context: grpc.aio.ServicerContext) -> Any:
        verified = await _verify_metadata_jwt(context, verifier)
        token: Token[JWTPayload | None] | None = None
        behalf_token: Token[int | None] | None = None
        bearer_token: Token[VerifiedBearer | None] | None = None
        try:
            if verified is not None:
                payload, raw = verified
                token = _CURRENT_JWT_PAYLOAD.set(payload)
                # On-behalf id is only honoured for authenticated bot callers;
                # for anyone else the metadata is ignored so it cannot
                # impersonate.
                if bool(getattr(payload, "is_bot", False)):
                    on_behalf = _read_on_behalf(context.invocation_metadata())
                    if on_behalf is not None:
                        behalf_token = _CURRENT_ON_BEHALF.set(on_behalf)
                if retain_bearer:
                    bearer_token = _VERIFIED_BEARER.set(VerifiedBearer(raw))
            return await inner_unary_unary(request, context)
        finally:
            if bearer_token is not None:
                _VERIFIED_BEARER.reset(bearer_token)
            if behalf_token is not None:
                _CURRENT_ON_BEHALF.reset(behalf_token)
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
) -> tuple[JWTPayload, str] | None:
    """Return the accepted payload and the raw token it was decoded from."""
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
        # A verifier may quote what it rejected; the token never reaches a log.
        reason = str(exc).replace(token, "<redacted>") if token else str(exc)
        logger.warning("gRPC inbound JWT failed verification: %s", reason)
        await context.abort(
            grpc.StatusCode.UNAUTHENTICATED,
            "invalid or expired authorization JWT",
        )
        return None  # unreachable — abort raises
    return payload, token


def _read_authorization(
    metadata: Sequence[tuple[str, str | bytes]] | None,
) -> str | None:
    if metadata is None:
        return None
    for key, value in metadata:
        if str(key).lower() == _AUTHORIZATION_HEADER:
            return value.decode("utf-8") if isinstance(value, bytes) else str(value)
    return None


def _read_on_behalf(
    metadata: Sequence[tuple[str, str | bytes]] | None,
) -> int | None:
    if metadata is None:
        return None
    for key, value in metadata:
        if str(key).lower() == _ON_BEHALF_METADATA:
            raw = value.decode("utf-8") if isinstance(value, bytes) else str(value)
            try:
                return int(raw.strip())
            except (TypeError, ValueError):
                return None
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
    "VerifiedBearer",
    "VerifiedBearerHandoffClientInterceptor",
    "current_jwt_payload",
    "current_on_behalf",
    "effective_user_id",
    "forwarded_jwt",
    "forwarded_on_behalf",
    "reset_forwarded_jwt",
    "reset_forwarded_on_behalf",
    "set_forwarded_jwt",
    "set_forwarded_on_behalf",
    "verified_bearer_retained",
    "verified_bearer_scope",
]
