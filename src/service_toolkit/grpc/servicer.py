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
    "AuthPermissionDeniedError": grpc.StatusCode.PERMISSION_DENIED,
    "DuplicateResourceError": grpc.StatusCode.ALREADY_EXISTS,
    "RateLimitError": grpc.StatusCode.RESOURCE_EXHAUSTED,
    "NotAuthorizedException": grpc.StatusCode.UNAUTHENTICATED,
}


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
            cls_name = type(exc).__name__
            status = _ERROR_CLASS_TO_STATUS.get(cls_name)
            if status is not None:
                detail = str(exc) or cls_name
                await context.abort(status, detail)
            # Unknown exceptions → INTERNAL
            logger.exception("Unhandled error in %s", func.__qualname__)
            await context.abort(grpc.StatusCode.INTERNAL, "Internal server error")

    return wrapper


__all__ = [
    "abort_invalid",
    "abort_not_found",
    "db_session",
    "grpc_error_handler",
]
