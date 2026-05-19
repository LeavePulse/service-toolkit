"""Tests for service_toolkit.grpc.jwt_forwarding."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import grpc
import pytest

from service_toolkit.grpc.jwt_forwarding import (
    JwtContextServerInterceptor,
    JwtForwardingClientInterceptor,
    current_jwt_payload,
    forwarded_jwt,
    reset_forwarded_jwt,
    set_forwarded_jwt,
)


@dataclass
class _StubJWTPayload:
    sub: str
    roles: list[str]


def test_forwarded_jwt_default_is_none() -> None:
    assert forwarded_jwt() is None


def test_set_and_reset_forwarded_jwt() -> None:
    token = set_forwarded_jwt("eyJhbGc")
    try:
        assert forwarded_jwt() == "eyJhbGc"
    finally:
        reset_forwarded_jwt(token)
    assert forwarded_jwt() is None


def test_set_forwarded_jwt_empty_string_becomes_none() -> None:
    token = set_forwarded_jwt("")
    try:
        assert forwarded_jwt() is None
    finally:
        reset_forwarded_jwt(token)


@pytest.mark.asyncio
async def test_client_interceptor_adds_authorization_header() -> None:
    interceptor = JwtForwardingClientInterceptor()
    captured_details: list[grpc.aio.ClientCallDetails] = []

    async def fake_continuation(
        details: grpc.aio.ClientCallDetails, request: Any
    ) -> str:
        captured_details.append(details)
        return "ok"

    details = grpc.aio.ClientCallDetails(
        method="/svc/M",
        timeout=None,
        metadata=(),
        credentials=None,
        wait_for_ready=None,
    )

    token = set_forwarded_jwt("test.jwt.value")
    try:
        result = await interceptor.intercept_unary_unary(
            fake_continuation, details, request="r"
        )
    finally:
        reset_forwarded_jwt(token)

    assert result == "ok"
    assert len(captured_details) == 1
    metadata = list(captured_details[0].metadata or [])
    assert ("authorization", "Bearer test.jwt.value") in metadata


@pytest.mark.asyncio
async def test_client_interceptor_no_op_without_jwt() -> None:
    interceptor = JwtForwardingClientInterceptor()
    captured_details: list[grpc.aio.ClientCallDetails] = []

    async def fake_continuation(
        details: grpc.aio.ClientCallDetails, request: Any
    ) -> str:
        captured_details.append(details)
        return "ok"

    details = grpc.aio.ClientCallDetails(
        method="/svc/M",
        timeout=None,
        metadata=(("x-other", "v"),),
        credentials=None,
        wait_for_ready=None,
    )

    await interceptor.intercept_unary_unary(
        fake_continuation, details, request="r"
    )

    assert captured_details[0] is details  # unchanged details forwarded verbatim


@pytest.mark.asyncio
async def test_client_interceptor_skips_when_caller_set_authorization() -> None:
    interceptor = JwtForwardingClientInterceptor()
    captured_details: list[grpc.aio.ClientCallDetails] = []

    async def fake_continuation(
        details: grpc.aio.ClientCallDetails, request: Any
    ) -> str:
        captured_details.append(details)
        return "ok"

    details = grpc.aio.ClientCallDetails(
        method="/svc/M",
        timeout=None,
        metadata=(("Authorization", "Bearer caller-supplied"),),
        credentials=None,
        wait_for_ready=None,
    )
    token = set_forwarded_jwt("forwarded-jwt")
    try:
        await interceptor.intercept_unary_unary(
            fake_continuation, details, request="r"
        )
    finally:
        reset_forwarded_jwt(token)

    metadata = list(captured_details[0].metadata or [])
    assert ("Authorization", "Bearer caller-supplied") in metadata
    assert ("authorization", "Bearer forwarded-jwt") not in metadata


@pytest.mark.asyncio
async def test_server_interceptor_decodes_jwt_into_contextvar() -> None:
    captured_payloads: list[Any] = []

    async def inner(request: Any, context: grpc.aio.ServicerContext) -> str:
        captured_payloads.append(current_jwt_payload())
        return "served"

    inner_handler = grpc.unary_unary_rpc_method_handler(
        inner,
        request_deserializer=lambda b: b,
        response_serializer=lambda s: s.encode(),
    )

    fake_payload = _StubJWTPayload(sub="42", roles=["developer"])
    verifier = MagicMock()

    async def verify(token: str) -> _StubJWTPayload:
        assert token == "good.jwt"
        return fake_payload

    verifier.verify = verify

    interceptor = JwtContextServerInterceptor(verifier)

    async def continuation(
        details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler:
        return inner_handler

    handler_details = MagicMock()
    handler_details.method = "/leavepulse.test.v1.Service/Method"
    wrapped = await interceptor.intercept_service(continuation, handler_details)

    ctx = MagicMock()
    ctx.invocation_metadata = lambda: [("authorization", "Bearer good.jwt")]

    result = await wrapped.unary_unary(b"req", ctx)
    assert result == "served"
    assert captured_payloads == [fake_payload]
    # contextvar is reset after the handler completes
    assert current_jwt_payload() is None


@pytest.mark.asyncio
async def test_server_interceptor_passes_through_without_authorization() -> None:
    captured_payloads: list[Any] = []

    async def inner(request: Any, context: grpc.aio.ServicerContext) -> str:
        captured_payloads.append(current_jwt_payload())
        return "anon"

    inner_handler = grpc.unary_unary_rpc_method_handler(inner)
    verifier = MagicMock()
    verifier.verify = MagicMock()

    interceptor = JwtContextServerInterceptor(verifier)

    async def continuation(
        details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler:
        return inner_handler

    handler_details = MagicMock()
    handler_details.method = "/leavepulse.test.v1.Service/Anon"
    wrapped = await interceptor.intercept_service(continuation, handler_details)

    ctx = MagicMock()
    ctx.invocation_metadata = lambda: []

    result = await wrapped.unary_unary(b"req", ctx)
    assert result == "anon"
    assert captured_payloads == [None]
    verifier.verify.assert_not_called()


@pytest.mark.asyncio
async def test_server_interceptor_aborts_on_invalid_jwt() -> None:
    aborted: dict[str, Any] = {}

    async def abort(code: Any, msg: str) -> None:
        aborted["code"] = code
        aborted["msg"] = msg
        raise asyncio.CancelledError("abort raised")

    async def inner(request: Any, context: grpc.aio.ServicerContext) -> str:
        raise RuntimeError("should not be reached")

    inner_handler = grpc.unary_unary_rpc_method_handler(inner)

    verifier = MagicMock()

    async def verify(_token: str) -> None:
        raise ValueError("bad signature")

    verifier.verify = verify

    interceptor = JwtContextServerInterceptor(verifier)

    async def continuation(
        details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler:
        return inner_handler

    handler_details = MagicMock()
    handler_details.method = "/leavepulse.test.v1.Service/Method"
    wrapped = await interceptor.intercept_service(continuation, handler_details)

    ctx = MagicMock()
    ctx.invocation_metadata = lambda: [("authorization", "Bearer broken")]
    ctx.abort = abort

    with pytest.raises(asyncio.CancelledError):
        await wrapped.unary_unary(b"req", ctx)
    assert aborted["code"] == grpc.StatusCode.UNAUTHENTICATED


@pytest.mark.asyncio
async def test_server_interceptor_skips_health_and_reflection() -> None:
    async def continuation(
        details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler | None:
        return None  # ignored — we only check that we got asked

    verifier = MagicMock()
    interceptor = JwtContextServerInterceptor(verifier)

    health_details = MagicMock()
    health_details.method = "/grpc.health.v1.Health/Check"
    result = await interceptor.intercept_service(continuation, health_details)
    assert result is None

    reflection_details = MagicMock()
    reflection_details.method = "/grpc.reflection.v1alpha.ServerReflection/Info"
    result = await interceptor.intercept_service(continuation, reflection_details)
    assert result is None
