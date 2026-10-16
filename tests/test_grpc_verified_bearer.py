"""Handing a verified inbound user JWT to one explicitly configured client.

The rules pinned here: the raw bearer is retained only when the server opted
in and only after its verifier accepted it; it lives exactly as long as the
RPC (or the HTTP scope) that verified it, also when the handler raises; only a
channel built with ``verified_bearer_handoff=True`` sends it, and every other
channel sends no ``authorization`` from it even while it is set; concurrent
calls never see each other's token; and the token never shows up in a log
line, an exception text or a repr. The server tests use real connections.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import grpc
import pytest
import pytest_asyncio
from google.protobuf.wrappers_pb2 import StringValue

from service_toolkit.grpc import channels as grpc_channels
from service_toolkit.grpc import jwt_forwarding
from service_toolkit.grpc.channels import build_shared_channel, close_shared_channels
from service_toolkit.grpc.jwt_forwarding import (
    JwtContextServerInterceptor,
    VerifiedBearerHandoffClientInterceptor,
    current_jwt_payload,
    reset_forwarded_jwt,
    set_forwarded_jwt,
    verified_bearer_retained,
    verified_bearer_scope,
)
from service_toolkit.grpc.server import build_grpc_lifecycle

PLATFORM = "platform-token"
FRONT = "bearer.test.v1.Front"
ECHO = "bearer.test.v1.Echo"
NONE = "<none>"

_CODEC_IN = {
    "request_deserializer": StringValue.FromString,
    "response_serializer": StringValue.SerializeToString,
}
_CODEC_OUT = {
    "request_serializer": StringValue.SerializeToString,
    "response_deserializer": StringValue.FromString,
}


@dataclass
class _Payload:
    sub: str


class _Verifier:
    """Accepts ``good.<sub>`` and rejects anything else, naming the token.

    The rejection message deliberately contains the token, so the tests can
    show that the interceptor never lets a verifier's text carry it to a log.
    """

    async def verify(self, token: str) -> _Payload:
        if not token.startswith("good."):
            raise ValueError(f"rejected token {token}")
        return _Payload(sub=token.removeprefix("good."))


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _authorization(context: grpc.aio.ServicerContext) -> str:
    seen = [
        str(value)
        for key, value in context.invocation_metadata() or ()
        if key.lower() == "authorization"
    ]
    return "|".join(seen) or NONE


# --- the downstream: answers with the authorization it received -----------------


@dataclass
class _Downstream:
    target: str
    seen: list[str]


@pytest_asyncio.fixture
async def downstream() -> AsyncIterator[_Downstream]:
    seen: list[str] = []

    async def echo(
        request: StringValue, context: grpc.aio.ServicerContext
    ) -> StringValue:
        received = _authorization(context)
        seen.append(received)
        if request.value == "deny":
            await context.abort(grpc.StatusCode.PERMISSION_DENIED, "denied")
        return StringValue(value=received)

    server = grpc.aio.server()
    server.add_generic_rpc_handlers(
        (
            grpc.method_handlers_generic_handler(
                ECHO, {"Echo": grpc.unary_unary_rpc_method_handler(echo, **_CODEC_IN)}
            ),
        )
    )
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        yield _Downstream(f"127.0.0.1:{port}", seen)
    finally:
        await server.stop(None)


# --- the front: relays to the downstream on an opted and a plain channel ----------


@pytest.fixture(autouse=True)
def _reset_shared_channels() -> None:
    grpc_channels._SHARED_CHANNELS.clear()  # noqa: SLF001
    grpc_channels._SHARED_CHANNEL_SPECS.clear()  # noqa: SLF001


async def _echo(channel: grpc.aio.Channel, value: str) -> str:
    call = channel.unary_unary(f"/{ECHO}/Echo", **_CODEC_OUT)
    return (await call(StringValue(value=value), timeout=5)).value


def _front_registrar(target: str, retained_log: list[bool]):  # type: ignore[no-untyped-def]
    opted = build_shared_channel(
        key="bearer_test.opted", target=target, verified_bearer_handoff=True
    )
    plain = build_shared_channel(key="bearer_test.plain", target=target)

    async def relay(request: StringValue, _context: object) -> StringValue:
        retained_log.append(verified_bearer_retained())
        if request.value == "deny":
            # The downstream refuses; the handler lets the error escape.
            await _echo(opted, "deny")
        if request.value == "raise":
            raise RuntimeError("handler failed")
        await asyncio.sleep(0.01)
        through_opted = await _echo(opted, "x")
        await asyncio.sleep(0)
        through_plain = await _echo(plain, "x")
        return StringValue(value=f"{through_opted}#{through_plain}")

    def register(server: grpc.aio.Server) -> None:
        server.add_generic_rpc_handlers(
            (
                grpc.method_handlers_generic_handler(
                    FRONT,
                    {"Relay": grpc.unary_unary_rpc_method_handler(relay, **_CODEC_IN)},
                ),
            )
        )

    return register


async def _start_front(
    downstream: _Downstream, *, retain: bool, retained_log: list[bool]
):  # type: ignore[no-untyped-def]
    port = _free_port()
    startup, shutdown = build_grpc_lifecycle(
        service_name="bearer-test",
        port=port,
        internal_token=PLATFORM,
        jwt_verifier=_Verifier(),  # type: ignore[arg-type]
        retain_verified_bearer=retain,
        reflection_enabled=False,
        registrars=[_front_registrar(downstream.target, retained_log)],
    )
    await startup()
    return f"127.0.0.1:{port}", shutdown


@dataclass
class _Front:
    target: str
    retained: list[bool]


@pytest_asyncio.fixture
async def front(downstream: _Downstream) -> AsyncIterator[_Front]:
    retained: list[bool] = []
    target, shutdown = await _start_front(
        downstream, retain=True, retained_log=retained
    )
    try:
        yield _Front(target, retained)
    finally:
        await shutdown()
        await close_shared_channels()


@pytest_asyncio.fixture
async def default_front(downstream: _Downstream) -> AsyncIterator[_Front]:
    retained: list[bool] = []
    target, shutdown = await _start_front(
        downstream, retain=False, retained_log=retained
    )
    try:
        yield _Front(target, retained)
    finally:
        await shutdown()
        await close_shared_channels()


async def _relay(target: str, value: str, bearer: str | None) -> str:
    metadata = [("x-internal-token", PLATFORM)]
    if bearer is not None:
        metadata.append(("authorization", bearer))
    async with grpc.aio.insecure_channel(target) as channel:
        call = channel.unary_unary(f"/{FRONT}/Relay", **_CODEC_OUT)
        return (
            await call(StringValue(value=value), metadata=metadata, timeout=5)
        ).value


# --- over the wire --------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_opted_in_channel_hands_the_verified_bearer_to_its_target(
    front: _Front, downstream: _Downstream
) -> None:
    opted, _plain = (await _relay(front.target, "x", "Bearer good.alice")).split("#")
    assert opted == "Bearer good.alice"
    assert front.retained == [True]


@pytest.mark.asyncio
async def test_a_channel_that_did_not_opt_in_sends_nothing_inside_the_same_rpc(
    front: _Front, downstream: _Downstream
) -> None:
    _opted, plain = (await _relay(front.target, "x", "Bearer good.alice")).split("#")
    assert plain == NONE


@pytest.mark.asyncio
async def test_a_default_server_retains_nothing_so_the_opted_channel_sends_nothing(
    default_front: _Front, downstream: _Downstream
) -> None:
    answer = await _relay(default_front.target, "x", "Bearer good.alice")
    assert answer == f"{NONE}#{NONE}"
    assert default_front.retained == [False]
    assert downstream.seen == [NONE, NONE]


@pytest.mark.asyncio
async def test_an_anonymous_call_hands_off_nothing(
    front: _Front, downstream: _Downstream
) -> None:
    assert await _relay(front.target, "x", None) == f"{NONE}#{NONE}"
    assert front.retained == [False]


@pytest.mark.asyncio
@pytest.mark.parametrize("bearer", ["Bearer bad.mallory", "Basic good.alice"])
async def test_an_invalid_inbound_jwt_is_rejected_and_nothing_is_retained(
    front: _Front, downstream: _Downstream, bearer: str
) -> None:
    with pytest.raises(grpc.aio.AioRpcError) as refused:
        await _relay(front.target, "x", bearer)
    assert refused.value.code() is grpc.StatusCode.UNAUTHENTICATED
    assert front.retained == []
    assert downstream.seen == []


@pytest.mark.asyncio
async def test_concurrent_rpcs_never_see_each_others_token(
    front: _Front, downstream: _Downstream
) -> None:
    users = [f"user{i}" for i in range(20)]
    answers = await asyncio.gather(
        *(_relay(front.target, "x", f"Bearer good.{user}") for user in users)
    )
    for user, answer in zip(users, answers, strict=True):
        assert answer == f"Bearer good.{user}#{NONE}"


@pytest.mark.asyncio
async def test_the_token_is_in_no_log_and_no_exception_text_on_failure_paths(
    front: _Front, downstream: _Downstream, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    texts: list[str] = []
    for value, bearer in (
        ("deny", "Bearer good.secret-deny"),
        ("raise", "Bearer good.secret-raise"),
        ("x", "Bearer bad.secret-rejected"),
    ):
        with pytest.raises(grpc.aio.AioRpcError) as failed:
            await _relay(front.target, value, bearer)
        error = failed.value
        texts += [
            str(error),
            repr(error),
            str(error.details()),
            error.debug_error_string(),
        ]
    assert "Bearer good.secret-deny" in downstream.seen  # it really was handed off
    joined = "\n".join(texts) + caplog.text
    for secret in ("secret-deny", "secret-raise", "secret-rejected"):
        assert secret not in joined


# --- the server wrapper, in the calling task -----------------------------------------


async def _wrapped(
    inner: Any, *, retain: bool, verifier: Any = None
) -> grpc.RpcMethodHandler:
    interceptor = JwtContextServerInterceptor(
        verifier or _Verifier(), retain_verified_bearer=retain
    )

    async def continuation(_details: object) -> grpc.RpcMethodHandler:
        return grpc.unary_unary_rpc_method_handler(inner)

    details = MagicMock()
    details.method = f"/{FRONT}/Relay"
    return await interceptor.intercept_service(continuation, details)


def _context(bearer: str) -> MagicMock:
    context = MagicMock()
    context.invocation_metadata = lambda: [("authorization", bearer)]

    async def abort(code: grpc.StatusCode, message: str) -> None:
        raise grpc.aio.AbortError(f"{code}: {message}")

    context.abort = abort
    return context


@pytest.mark.asyncio
async def test_the_bearer_is_reset_when_the_rpc_returns_and_when_it_raises() -> None:
    seen: list[bool] = []

    async def ok(_r: object, _c: object) -> str:
        seen.append(verified_bearer_retained())
        return "ok"

    async def fails(_r: object, _c: object) -> str:
        seen.append(verified_bearer_retained())
        raise RuntimeError("boom")

    assert (
        await (await _wrapped(ok, retain=True)).unary_unary(
            b"", _context("Bearer good.a")
        )
        == "ok"
    )
    assert not verified_bearer_retained() and current_jwt_payload() is None
    with pytest.raises(RuntimeError):
        await (await _wrapped(fails, retain=True)).unary_unary(
            b"", _context("Bearer good.a")
        )
    assert not verified_bearer_retained() and current_jwt_payload() is None
    assert seen == [True, True]


@pytest.mark.asyncio
async def test_a_rejected_bearer_is_never_retained_in_the_calling_task() -> None:
    async def never(_r: object, _c: object) -> str:
        raise AssertionError("reached")

    with pytest.raises(grpc.aio.AbortError):
        await (await _wrapped(never, retain=True)).unary_unary(
            b"", _context("Bearer bad.x")
        )
    assert not verified_bearer_retained()


@pytest.mark.asyncio
async def test_without_the_opt_in_the_wrapper_retains_nothing() -> None:
    seen: list[bool] = []

    async def inner(_r: object, _c: object) -> str:
        seen.append(verified_bearer_retained())
        return "ok"

    await (await _wrapped(inner, retain=False)).unary_unary(
        b"", _context("Bearer good.a")
    )
    assert seen == [False]


def test_the_retained_bearer_is_redacted_in_every_repr() -> None:
    with verified_bearer_scope("good.hidden-value"):
        held = jwt_forwarding._VERIFIED_BEARER.get()  # noqa: SLF001
        assert held is not None
        for text in (repr(held), str(held), f"{held!r}", f"{held}"):
            assert "hidden-value" not in text
        with pytest.raises(TypeError) as refused:
            import pickle

            pickle.dumps(held)
        assert "hidden-value" not in str(refused.value)


def test_lifecycle_refuses_retention_without_a_verifier() -> None:
    with pytest.raises(RuntimeError, match="retain_verified_bearer"):
        build_grpc_lifecycle(
            service_name="bearer-test",
            port=_free_port(),
            internal_token=PLATFORM,
            retain_verified_bearer=True,
        )


def test_a_channel_key_cannot_be_reused_with_a_different_handoff() -> None:
    build_shared_channel(key="bearer_test.reuse", target="x:1")
    with pytest.raises(RuntimeError, match="different configuration"):
        build_shared_channel(
            key="bearer_test.reuse", target="x:1", verified_bearer_handoff=True
        )


# --- the client interceptor --------------------------------------------------------


def _details(metadata: tuple[tuple[str, str], ...] = ()) -> grpc.aio.ClientCallDetails:
    return grpc.aio.ClientCallDetails(
        method="/svc/M",
        timeout=None,
        metadata=metadata,
        credentials=None,
        wait_for_ready=None,
    )


async def _sent(
    details: grpc.aio.ClientCallDetails,
    interceptor: grpc.aio.UnaryUnaryClientInterceptor | None = None,
) -> list[tuple[str, str]]:
    captured: list[grpc.aio.ClientCallDetails] = []

    async def continuation(sent: grpc.aio.ClientCallDetails, _request: object) -> str:
        captured.append(sent)
        return "ok"

    await (
        interceptor or VerifiedBearerHandoffClientInterceptor()
    ).intercept_unary_unary(continuation, details, "r")
    return list(captured[0].metadata or ())


@pytest.mark.asyncio
async def test_the_handoff_sends_the_bearer_only_inside_its_scope() -> None:
    assert await _sent(_details()) == []
    with verified_bearer_scope("good.alice"):
        assert await _sent(_details()) == [("authorization", "Bearer good.alice")]
    assert await _sent(_details()) == []


@pytest.mark.asyncio
async def test_the_platform_forwarding_interceptor_ignores_the_retained_bearer() -> (
    None
):
    plain = jwt_forwarding.JwtForwardingClientInterceptor()
    with verified_bearer_scope("good.alice"):
        assert await _sent(_details(), plain) == []
    # Its own path is unchanged.
    token = set_forwarded_jwt("platform.jwt")
    try:
        assert await _sent(_details(), plain) == [
            ("authorization", "Bearer platform.jwt")
        ]
    finally:
        reset_forwarded_jwt(token)


@pytest.mark.asyncio
async def test_the_handoff_never_writes_into_a_caller_supplied_authorization() -> None:
    supplied = (("Authorization", "Bearer caller"),)
    details = _details(supplied)
    with verified_bearer_scope("good.alice"):
        assert await _sent(details) == list(supplied)
    assert details.metadata == supplied


# --- the HTTP-side scope ----------------------------------------------------------------


def test_the_http_scope_resets_on_exit_on_error_and_when_nested() -> None:
    assert not verified_bearer_retained()
    with verified_bearer_scope("good.outer"):
        assert verified_bearer_retained()
        with pytest.raises(RuntimeError), verified_bearer_scope("good.inner"):
            raise RuntimeError("handler failed")
        held = jwt_forwarding._VERIFIED_BEARER.get()  # noqa: SLF001
        assert held is not None and held._reveal() == "good.outer"  # noqa: SLF001
    assert not verified_bearer_retained()


@pytest.mark.parametrize("token", ["", "   ", "good.a b", "good.a\nb"])
def test_the_http_scope_refuses_a_malformed_token_without_echoing_it(
    token: str,
) -> None:
    with pytest.raises(ValueError) as refused:
        with verified_bearer_scope(token):
            pass
    if token.strip():
        assert token.strip() not in str(refused.value)
    assert not verified_bearer_retained()


@pytest.mark.asyncio
async def test_concurrent_http_scopes_are_isolated() -> None:
    interceptor = VerifiedBearerHandoffClientInterceptor()

    async def request(user: str) -> list[tuple[str, str]]:
        with verified_bearer_scope(f"good.{user}"):
            await asyncio.sleep(0.001)
            return await _sent(_details(), interceptor)

    users = [f"u{i}" for i in range(20)]
    sent = await asyncio.gather(*(request(user) for user in users))
    for user, metadata in zip(users, sent, strict=True):
        assert metadata == [("authorization", f"Bearer good.{user}")]
    assert not verified_bearer_retained()
