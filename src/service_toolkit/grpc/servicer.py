"""Base utilities for gRPC servicer implementations.

Provides the same patterns as Litestar controllers but for gRPC:
- Automatic DB session management via ``db_session`` context manager
- Standardised error → gRPC status code mapping
"""

from __future__ import annotations

import functools
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import grpc

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Error mapping: awesome-errors error codes → gRPC status codes.
# Mirrors the HTTP → gRPC table from the migration plan.
# ---------------------------------------------------------------------------

_ERROR_CLASS_TO_STATUS: dict[str, grpc.StatusCode] = {
    "ResourceNotFoundError": grpc.StatusCode.NOT_FOUND,
    "InvalidInputError": grpc.StatusCode.INVALID_ARGUMENT,
    "InvalidFormatError": grpc.StatusCode.INVALID_ARGUMENT,
    "AuthRequiredError": grpc.StatusCode.UNAUTHENTICATED,
    "AuthInvalidTokenError": grpc.StatusCode.UNAUTHENTICATED,
    "AuthPermissionDeniedError": grpc.StatusCode.PERMISSION_DENIED,
    "DuplicateResourceError": grpc.StatusCode.ALREADY_EXISTS,
    "RateLimitError": grpc.StatusCode.RESOURCE_EXHAUSTED,
    "NotAuthorizedException": grpc.StatusCode.UNAUTHENTICATED,
}

# HTTP status → gRPC status used as a generic fallback for any awesome-errors
# ``AppError`` that does not match a known class name above. This lets the
# server-side domain-error interceptor map *every* domain error to a precise
# gRPC code (and therefore a precise BFF HTTP code) even for servicer methods
# that are not individually decorated with ``@grpc_error_handler``.
_HTTP_STATUS_TO_GRPC: dict[int, grpc.StatusCode] = {
    400: grpc.StatusCode.INVALID_ARGUMENT,
    401: grpc.StatusCode.UNAUTHENTICATED,
    403: grpc.StatusCode.PERMISSION_DENIED,
    404: grpc.StatusCode.NOT_FOUND,
    409: grpc.StatusCode.ALREADY_EXISTS,
    422: grpc.StatusCode.INVALID_ARGUMENT,
    429: grpc.StatusCode.RESOURCE_EXHAUSTED,
    501: grpc.StatusCode.UNIMPLEMENTED,
    503: grpc.StatusCode.UNAVAILABLE,
    504: grpc.StatusCode.DEADLINE_EXCEEDED,
}


def _grpc_status_for_exception(exc: BaseException) -> grpc.StatusCode | None:
    """Resolve the gRPC status for a domain exception, if it is one.

    Resolution order: explicit class-name table, then the awesome-errors
    HTTP ``status_code`` mapping. Returns ``None`` for exceptions that are
    not recognised domain errors (callers then fall back to ``INTERNAL``).
    """
    status = _ERROR_CLASS_TO_STATUS.get(type(exc).__name__)
    if status is not None:
        return status
    http_status = getattr(exc, "status_code", None)
    if isinstance(http_status, int):
        mapped = _HTTP_STATUS_TO_GRPC.get(http_status)
        if mapped is not None:
            return mapped
        if 400 <= http_status < 500:
            return grpc.StatusCode.INVALID_ARGUMENT
        if http_status >= 500:
            return grpc.StatusCode.INTERNAL
    return None


async def abort_not_found(
    context: grpc.aio.ServicerContext,
    resource: str,
    resource_id: object = "",
) -> None:
    """Abort with NOT_FOUND — convenience shortcut used by every servicer."""
    detail = f"{resource} not found"
    if resource_id:
        detail = f"{resource} {resource_id} not found"
    await context.abort(grpc.StatusCode.NOT_FOUND, detail)


async def abort_invalid(
    context: grpc.aio.ServicerContext,
    detail: str,
) -> None:
    """Abort with INVALID_ARGUMENT."""
    await context.abort(grpc.StatusCode.INVALID_ARGUMENT, detail)


@asynccontextmanager
async def db_session(
    session_maker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Yield an ``AsyncSession`` with automatic error → gRPC status mapping.

    Usage inside a servicer method::

        async with db_session(self._sm) as db:
            server = await db.get(Server, request.server_id)
    """
    async with session_maker() as session:
        yield session


def grpc_error_handler(func: Any) -> Any:
    """Decorator that catches awesome-errors exceptions and maps to gRPC aborts.

    Apply to individual RPC methods::

        @grpc_error_handler
        async def GetServer(self, request, context):
            ...
    """

    @functools.wraps(func)
    async def wrapper(self: Any, request: Any, context: grpc.aio.ServicerContext) -> Any:
        try:
            return await func(self, request, context)
        except grpc.aio.AbortError:
            # Already aborted — re-raise as-is
            raise
        except Exception as exc:
            status = _grpc_status_for_exception(exc)
            if status is not None:
                detail = str(exc) or type(exc).__name__
                await context.abort(status, detail)
            # Unknown exceptions → INTERNAL
            logger.exception("Unhandled error in %s", func.__qualname__)
            await context.abort(grpc.StatusCode.INTERNAL, "Internal server error")

    return wrapper


class DomainErrorServerInterceptor(grpc.aio.ServerInterceptor):
    """Map awesome-errors domain exceptions to precise gRPC status codes.

    Installed once per server by :func:`build_grpc_lifecycle`, this is the
    server-side counterpart of the client's ``translate_grpc_error``: it
    ensures a servicer that raises e.g. ``AuthRequiredError`` or
    ``ResourceNotFoundError`` terminates the RPC with ``UNAUTHENTICATED`` /
    ``NOT_FOUND`` instead of leaking an ``UNKNOWN`` "Unexpected … raised by
    servicer method" error — even when the method is not individually
    wrapped with :func:`grpc_error_handler`.

    Unrecognised exceptions are left to propagate unchanged so the gRPC
    framework still reports them as ``UNKNOWN``/``INTERNAL`` and they remain
    visible in tracebacks.
    """

    async def intercept_service(
        self,
        continuation: Any,
        handler_call_details: grpc.HandlerCallDetails,
    ) -> Any:
        method = handler_call_details.method or ""
        if "grpc.health" in method or "grpc.reflection" in method:
            return await continuation(handler_call_details)

        handler = await continuation(handler_call_details)
        if handler is None or handler.unary_unary is None:
            return handler

        inner = handler.unary_unary

        async def _wrapped(request: Any, context: grpc.aio.ServicerContext) -> Any:
            try:
                return await inner(request, context)
            except grpc.aio.AbortError:
                raise
            except Exception as exc:  # noqa: BLE001 — re-raised when not a domain error
                status = _grpc_status_for_exception(exc)
                if status is None:
                    raise
                await context.abort(status, str(exc) or type(exc).__name__)

        return grpc.unary_unary_rpc_method_handler(
            _wrapped,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )


__all__ = [
    "DomainErrorServerInterceptor",
    "abort_invalid",
    "abort_not_found",
    "db_session",
    "grpc_error_handler",
]
