"""Tests for server-side domain-error → gRPC status mapping."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest

from service_toolkit.grpc.servicer import (
    DomainErrorServerInterceptor,
    _grpc_status_for_exception,
)


class _AuthRequiredError(Exception):
    status_code = 401


class _ResourceNotFoundError(Exception):
    status_code = 404


class _PlainHttpError(Exception):
    status_code = 503


class _NotADomainError(Exception):
    pass


def test_status_by_class_name() -> None:
    # Class-name table takes precedence (named exactly as awesome-errors).
    class AuthRequiredError(Exception):
        pass

    assert (
        _grpc_status_for_exception(AuthRequiredError())
        is grpc.StatusCode.UNAUTHENTICATED
    )


def test_status_by_http_status_code() -> None:
    assert (
        _grpc_status_for_exception(_ResourceNotFoundError())
        is grpc.StatusCode.NOT_FOUND
    )
    assert (
        _grpc_status_for_exception(_PlainHttpError()) is grpc.StatusCode.UNAVAILABLE
    )


def test_unknown_4xx_falls_back_to_invalid_argument() -> None:
    class _Weird(Exception):
        status_code = 418

    assert _grpc_status_for_exception(_Weird()) is grpc.StatusCode.INVALID_ARGUMENT


def test_non_domain_error_returns_none() -> None:
    assert _grpc_status_for_exception(_NotADomainError()) is None
    assert _grpc_status_for_exception(ValueError("x")) is None


@pytest.mark.asyncio
async def test_interceptor_aborts_with_mapped_status() -> None:
    interceptor = DomainErrorServerInterceptor()

    async def _servicer(request: Any, context: Any) -> Any:
        raise _ResourceNotFoundError()

    handler = grpc.unary_unary_rpc_method_handler(_servicer)
    continuation = AsyncMock(return_value=handler)
    details = MagicMock()
    details.method = "/pkg.Service/Method"

    wrapped = await interceptor.intercept_service(continuation, details)

    context = MagicMock()
    context.abort = AsyncMock(return_value=None)

    await wrapped.unary_unary(object(), context)

    context.abort.assert_awaited_once()
    assert context.abort.await_args.args[0] is grpc.StatusCode.NOT_FOUND


@pytest.mark.asyncio
async def test_interceptor_reraises_non_domain_error() -> None:
    interceptor = DomainErrorServerInterceptor()

    async def _servicer(request: Any, context: Any) -> Any:
        raise _NotADomainError("boom")

    handler = grpc.unary_unary_rpc_method_handler(_servicer)
    continuation = AsyncMock(return_value=handler)
    details = MagicMock()
    details.method = "/pkg.Service/Method"

    wrapped = await interceptor.intercept_service(continuation, details)
    context = MagicMock()
    context.abort = AsyncMock()

    with pytest.raises(_NotADomainError):
        await wrapped.unary_unary(object(), context)

    context.abort.assert_not_awaited()
