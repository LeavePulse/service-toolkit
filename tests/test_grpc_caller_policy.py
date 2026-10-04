"""Per-caller, per-method authorization: one decision, no fallback.

The rules pinned here: a policy-listed method is decided by the policy alone
(the platform token, the exemptions and the alternate verifier never reach
it); callers are told apart by secrets that are non-empty and unique; an
unknown credential is UNAUTHENTICATED and a known caller the method does not
list is PERMISSION_DENIED; every handler kind is refused with a status the
client can read; and the caller label never outlives the call it belongs to.
The server tests use real connections, because only a connection shows what
a client actually receives.
"""

from __future__ import annotations

import asyncio
import socket

import grpc
import pytest
import pytest_asyncio
from grpc_health.v1 import health_pb2, health_pb2_grpc
from grpc_reflection.v1alpha import reflection_pb2, reflection_pb2_grpc

from service_toolkit.grpc.authorization import (
    CallerCredentials,
    CallerPolicy,
    CallerPolicyError,
    Verdict,
    as_caller,
    current_caller,
    refusing,
)
from service_toolkit.grpc.interceptors import InternalTokenInterceptor
from service_toolkit.grpc.server import build_grpc_lifecycle

PLATFORM = "platform-token"
CONTROL = "control-secret"
NETWORK = "network-secret"
CHECK = "/grpc.health.v1.Health/Check"
REFLECT = "/grpc.reflection.v1alpha.ServerReflection/ServerReflectionInfo"


def _callers() -> CallerCredentials:
    return CallerCredentials({"control-service": CONTROL, "network-service": NETWORK})


def _policy(**rules: list[str]) -> CallerPolicy:
    return CallerPolicy(
        rules or {"/pkg.Svc/Write": ["control-service"]},
        _callers(),
    )


# --- credentials -----------------------------------------------------------


@pytest.mark.parametrize("secret", ["", " padded", "padded "])
def test_an_empty_or_padded_secret_is_refused(secret: str) -> None:
    with pytest.raises(CallerPolicyError):
        CallerCredentials({"control-service": secret})


def test_two_callers_sharing_a_secret_is_refused_without_saying_it() -> None:
    with pytest.raises(CallerPolicyError) as refused:
        CallerCredentials({"control-service": "same", "network-service": "same"})
    assert "same" not in str(refused.value).replace("share", "")
    assert "control-service" in str(refused.value)


@pytest.mark.parametrize("name", ["", "Control", "control service", "-x"])
def test_a_caller_name_is_a_lowercase_service_name(name: str) -> None:
    with pytest.raises(CallerPolicyError):
        CallerCredentials({name: "s"})


def test_a_presented_secret_names_exactly_one_caller() -> None:
    callers = _callers()
    assert callers.resolve(CONTROL) == "control-service"
    assert callers.resolve(NETWORK) == "network-service"
    for other in (None, "", PLATFORM, CONTROL + "x", CONTROL[:-1]):
        assert callers.resolve(other) is None


def test_no_secret_appears_in_a_repr() -> None:
    text = repr(_callers()) + repr(_policy())
    assert CONTROL not in text and NETWORK not in text
    assert "control-service" in text


# --- policy ------------------------------------------------------------------


@pytest.mark.parametrize(
    "method",
    [
        "Svc/Write",
        "pkg.Svc/Write",
        "/pkg.Svc/*",
        "/pkg.Svc/",
        "/Svc/Write",
        "/pkg.Svc/Write/",
    ],
)
def test_only_an_exact_full_method_path_is_accepted(method: str) -> None:
    with pytest.raises(CallerPolicyError):
        CallerPolicy({method: ["control-service"]}, _callers())


def test_a_method_admitting_nobody_is_refused() -> None:
    with pytest.raises(CallerPolicyError):
        CallerPolicy({"/pkg.Svc/Write": []}, _callers())


def test_a_caller_without_a_secret_is_refused() -> None:
    with pytest.raises(CallerPolicyError, match="billing-service"):
        CallerPolicy({"/pkg.Svc/Write": ["billing-service"]}, _callers())


def test_the_decision() -> None:
    policy = _policy()
    method = "/pkg.Svc/Write"
    assert policy.decide(method, CONTROL).verdict is Verdict.ADMIT
    assert policy.decide(method, CONTROL).caller == "control-service"
    assert policy.decide(method, NETWORK).verdict is Verdict.PERMISSION_DENIED
    for refused in (None, "", "garbage", PLATFORM):
        assert policy.decide(method, refused).verdict is Verdict.UNAUTHENTICATED


def test_a_listed_method_must_be_served() -> None:
    policy = CallerPolicy({CHECK: ["control-service"]}, _callers())
    policy.check_served(["grpc.health.v1.Health"])
    with pytest.raises(CallerPolicyError, match="not served"):
        policy.check_served(["leavepulse.other.v1.Other"])
    typo = CallerPolicy(
        {"/grpc.health.v1.Health/Chek": ["control-service"]}, _callers()
    )
    with pytest.raises(CallerPolicyError, match="no method Chek"):
        typo.check_served(["grpc.health.v1.Health"])
    unknown = CallerPolicy({"/no.such.v1.Svc/M": ["control-service"]}, _callers())
    with pytest.raises(CallerPolicyError, match="no descriptor"):
        unknown.check_served(["no.such.v1.Svc"])


# --- the interceptor's one decision --------------------------------------------


class _Details:
    def __init__(self, method: str, token: str | None) -> None:
        self.method = method
        self.invocation_metadata = (
            [("x-internal-token", token)] if token is not None else []
        )


async def _behaviour(_request: object, _context: object) -> str:
    return "served"


_HANDLER = grpc.unary_unary_rpc_method_handler(_behaviour)


async def _continuation(_details: object) -> grpc.RpcMethodHandler:
    return _HANDLER


async def _admitted(
    interceptor: InternalTokenInterceptor, method: str, token: str | None
) -> bool:
    handler = await interceptor.intercept_service(
        _continuation, _Details(method, token)
    )
    if handler is _HANDLER:
        return True
    # A policy admission wraps the handler; a refusal replaces it.
    return (
        await handler.unary_unary(None, None) == "served"
        if _is_wrapped(handler)
        else False
    )


def _is_wrapped(handler: grpc.RpcMethodHandler) -> bool:
    return (
        handler.unary_unary is not None
        and handler.unary_unary.__name__ == "_async_single"
    )


@pytest.mark.asyncio
async def test_the_platform_token_never_opens_a_listed_method() -> None:
    interceptor = InternalTokenInterceptor(PLATFORM, policy=_policy())
    assert not await _admitted(interceptor, "/pkg.Svc/Write", PLATFORM)
    assert await _admitted(interceptor, "/pkg.Svc/Write", CONTROL)
    # Unlisted methods keep the platform-token rule, and only that.
    assert await _admitted(interceptor, "/pkg.Svc/Other", PLATFORM)
    assert not await _admitted(interceptor, "/pkg.Svc/Other", CONTROL)


@pytest.mark.asyncio
async def test_neither_an_exemption_nor_the_alternate_verifier_reaches_a_listed_method() -> (
    None
):
    consulted: list[str] = []

    async def verifier(method: str, _token: str):  # type: ignore[no-untyped-def]
        consulted.append(method)
        return lambda handler: handler

    interceptor = InternalTokenInterceptor(
        PLATFORM,
        exempt_methods=["Svc/Write"],
        alternate_verifier=verifier,
        policy=_policy(),
    )
    assert not await _admitted(interceptor, "/pkg.Svc/Write", None)
    assert not await _admitted(interceptor, "/pkg.Svc/Write", "per-host-token")
    assert consulted == []


@pytest.mark.asyncio
async def test_health_listed_in_a_policy_is_decided_by_it() -> None:
    policy = CallerPolicy({CHECK: ["control-service"]}, _callers())
    interceptor = InternalTokenInterceptor(PLATFORM, policy=policy)
    assert not await _admitted(interceptor, CHECK, None)


@pytest.mark.asyncio
async def test_without_a_platform_token_a_listed_method_is_still_gated() -> None:
    interceptor = InternalTokenInterceptor(None, policy=_policy())
    assert not await _admitted(interceptor, "/pkg.Svc/Write", None)
    assert await _admitted(interceptor, "/pkg.Svc/Write", CONTROL)
    # As before the policy existed: no platform token, no gate on the rest.
    assert await _admitted(interceptor, "/pkg.Svc/Other", None)


def test_no_token_and_no_policy_is_still_refused() -> None:
    with pytest.raises(ValueError):
        InternalTokenInterceptor(None)


# --- handler kinds and the caller label ----------------------------------------


async def _unary(_r: object, _c: object) -> None:
    return None


async def _streaming(_r: object, _c: object):  # type: ignore[no-untyped-def]
    yield None


@pytest.mark.parametrize(
    ("factory", "request_streaming", "response_streaming", "behaviour"),
    [
        (grpc.unary_unary_rpc_method_handler, False, False, _unary),
        (grpc.unary_stream_rpc_method_handler, False, True, _streaming),
        (grpc.stream_unary_rpc_method_handler, True, False, _unary),
        (grpc.stream_stream_rpc_method_handler, True, True, _streaming),
    ],
)
def test_a_refusal_and_an_admission_keep_the_handlers_kind(
    factory,
    request_streaming,
    response_streaming,
    behaviour,  # type: ignore[no-untyped-def]
) -> None:
    handler = factory(behaviour, request_deserializer=bytes, response_serializer=bytes)
    for made in (
        refusing(handler, grpc.StatusCode.PERMISSION_DENIED, "no"),
        as_caller(handler, "control-service"),
    ):
        assert made.request_streaming is request_streaming
        assert made.response_streaming is response_streaming
        assert made.request_deserializer is bytes
        assert made.response_serializer is bytes


@pytest.mark.asyncio
async def test_concurrent_calls_each_see_only_their_own_caller() -> None:
    seen: dict[str, list[str | None]] = {}

    async def unary(request: str, _context: object) -> None:
        for _ in range(5):
            seen.setdefault(request, []).append(current_caller.get())
            await asyncio.sleep(0)

    async def stream(request: str, _context: object):  # type: ignore[no-untyped-def]
        for _ in range(5):
            seen.setdefault(request, []).append(current_caller.get())
            yield request
            await asyncio.sleep(0)

    one = as_caller(grpc.unary_unary_rpc_method_handler(unary), "control-service")
    two = as_caller(grpc.unary_unary_rpc_method_handler(unary), "network-service")
    three = as_caller(grpc.unary_stream_rpc_method_handler(stream), "network-service")

    async def drain() -> None:
        async for _ in three.unary_stream("c", None):
            await asyncio.sleep(0)

    await asyncio.gather(
        one.unary_unary("a", None), two.unary_unary("b", None), drain()
    )
    assert set(seen["a"]) == {"control-service"}
    assert set(seen["b"]) == {"network-service"}
    assert set(seen["c"]) == {"network-service"}
    assert current_caller.get() is None


@pytest.mark.asyncio
async def test_the_label_is_reset_when_the_handler_fails() -> None:
    async def fails(_r: object, _c: object) -> None:
        raise RuntimeError("boom")

    wrapped = as_caller(grpc.unary_unary_rpc_method_handler(fails), "control-service")
    with pytest.raises(RuntimeError):
        await wrapped.unary_unary(None, None)
    assert current_caller.get() is None


# --- over a real connection ---------------------------------------------------------
#
# A probe service with one method of each kind, declared in the descriptor pool
# so the startup check can find it. The bidirectional behaviour is a plain
# generator and the rest are async, because grpc.aio runs the two differently
# and the label has to reach both (the unary one is async because the domain
# error interceptor awaits every unary behaviour). Each answers with the
# caller label it saw.

_PROBE = "authz.test.v1.Probe"
_KINDS = ("Unary", "ServerStream", "ClientStream", "Bidi")


def _declare_probe() -> None:
    from google.protobuf import descriptor_pb2, descriptor_pool, wrappers_pb2

    pool = descriptor_pool.Default()
    try:
        pool.FindServiceByName(_PROBE)
        return
    except KeyError:
        pass
    pool.FindFileByName(wrappers_pb2.DESCRIPTOR.name)
    proto = descriptor_pb2.FileDescriptorProto(
        name="authz_test_probe.proto",
        package="authz.test.v1",
        dependency=[wrappers_pb2.DESCRIPTOR.name],
    )
    service = proto.service.add(name="Probe")
    for kind in _KINDS:
        service.method.add(
            name=kind,
            input_type=".google.protobuf.StringValue",
            output_type=".google.protobuf.StringValue",
            client_streaming=kind in ("ClientStream", "Bidi"),
            server_streaming=kind in ("ServerStream", "Bidi"),
        )
    pool.Add(proto)


#: Every label a probe behaviour saw: a refused call must never add one.
_SERVED: list[str] = []


def _label() -> str:
    label = current_caller.get() or "<none>"
    _SERVED.append(label)
    return label


def _register_probe(server: grpc.aio.Server) -> None:
    from google.protobuf.wrappers_pb2 import StringValue

    async def unary(_request, _context):  # type: ignore[no-untyped-def]
        return StringValue(value=_label())

    async def server_stream(_request, _context):  # type: ignore[no-untyped-def]
        for _ in range(2):
            yield StringValue(value=_label())
            await asyncio.sleep(0)

    async def client_stream(requests, _context):  # type: ignore[no-untyped-def]
        async for _ in requests:
            pass
        return StringValue(value=_label())

    def bidi(requests, _context):  # type: ignore[no-untyped-def]  # plain: thread pool
        for _ in requests:
            yield StringValue(value=_label())

    codec = {
        "request_deserializer": StringValue.FromString,
        "response_serializer": StringValue.SerializeToString,
    }
    server.add_generic_rpc_handlers(
        (
            grpc.method_handlers_generic_handler(
                _PROBE,
                {
                    "Unary": grpc.unary_unary_rpc_method_handler(unary, **codec),
                    "ServerStream": grpc.unary_stream_rpc_method_handler(
                        server_stream, **codec
                    ),
                    "ClientStream": grpc.stream_unary_rpc_method_handler(
                        client_stream, **codec
                    ),
                    "Bidi": grpc.stream_stream_rpc_method_handler(bidi, **codec),
                },
            ),
        )
    )


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest_asyncio.fixture
async def server():  # type: ignore[no-untyped-def]
    _declare_probe()
    port = _free_port()
    rules = {f"/{_PROBE}/{kind}": ["control-service"] for kind in _KINDS}
    rules[CHECK] = ["control-service"]
    rules[REFLECT] = ["control-service"]
    startup, shutdown = build_grpc_lifecycle(
        service_name="authz-test",
        port=port,
        internal_token=PLATFORM,
        service_names=[_PROBE],
        registrars=[_register_probe],
        caller_policy=CallerPolicy(rules, _callers()),
    )
    await startup()
    try:
        yield f"127.0.0.1:{port}"
    finally:
        await shutdown()


def _md(token: str | None) -> list[tuple[str, str]]:
    return [("x-internal-token", token)] if token is not None else []


async def _probe(channel: grpc.aio.Channel, kind: str, token: str | None):  # type: ignore[no-untyped-def]
    """The labels a call of *kind* answered with, or the status it ended on."""
    from google.protobuf.wrappers_pb2 import StringValue

    path = f"/{_PROBE}/{kind}"
    codec = {
        "request_serializer": StringValue.SerializeToString,
        "response_deserializer": StringValue.FromString,
    }
    out = StringValue(value="x")

    async def several():  # type: ignore[no-untyped-def]
        for _ in range(2):
            yield out

    try:
        if kind == "Unary":
            return [
                (
                    await channel.unary_unary(path, **codec)(
                        out, metadata=_md(token), timeout=5
                    )
                ).value
            ]
        if kind == "ClientStream":
            return [
                (
                    await channel.stream_unary(path, **codec)(
                        several(), metadata=_md(token), timeout=5
                    )
                ).value
            ]
        if kind == "ServerStream":
            call = channel.unary_stream(path, **codec)(
                out, metadata=_md(token), timeout=5
            )
        else:
            call = channel.stream_stream(path, **codec)(
                several(), metadata=_md(token), timeout=5
            )
        return [response.value async for response in call]
    except grpc.aio.AioRpcError as error:
        return error.code()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", _KINDS)
async def test_every_handler_kind_over_the_wire(server: str, kind: str) -> None:
    async with grpc.aio.insecure_channel(server) as channel:
        admitted = await _probe(channel, kind, CONTROL)
        assert admitted and set(admitted) == {"control-service"}
        _SERVED.clear()
        assert _refused_as(
            await _probe(channel, kind, NETWORK),
            kind,
            grpc.StatusCode.PERMISSION_DENIED,
        )
        assert _refused_as(
            await _probe(channel, kind, PLATFORM), kind, grpc.StatusCode.UNAUTHENTICATED
        )
        assert _refused_as(
            await _probe(channel, kind, None), kind, grpc.StatusCode.UNAUTHENTICATED
        )
        assert _SERVED == [], "a refused call reached the servicer"


def _refused_as(result: object, kind: str, code: grpc.StatusCode) -> bool:
    """*result* is the refusal *code*.

    One exception, which is grpc's and not ours: when a server ends a
    bidirectional call while the client is still writing its requests, the
    client can read "Internal error from Core" instead of the status. Measured
    with grpc 1.81 and no interceptor at all: a handler that only aborts gave
    31 of 300 such calls. The call is still refused (the servicer is never
    reached, which the caller checks), it is the client that loses the code.
    """
    if result is code:
        return True
    return kind == "Bidi" and result is grpc.StatusCode.INTERNAL


@pytest.mark.asyncio
async def test_concurrent_callers_over_the_wire_see_their_own_label(
    server: str,
) -> None:
    """Interleaved admitted and refused calls on every kind: each admitted one
    answers with its own caller, and nothing leaks between them."""
    async with grpc.aio.insecure_channel(server) as channel:
        results = await asyncio.gather(
            *(
                _probe(channel, kind, token)
                for _ in range(10)
                for kind in _KINDS
                for token in (CONTROL, NETWORK)
            )
        )
    for result in results:
        if isinstance(result, list):
            assert set(result) == {"control-service"}
        else:
            assert result in (
                grpc.StatusCode.PERMISSION_DENIED,
                grpc.StatusCode.INTERNAL,
            )
    assert "network-service" not in _SERVED and "<none>" not in _SERVED


@pytest.mark.asyncio
async def test_a_plain_unary_servicer_over_the_wire(server: str) -> None:
    """grpc's own (thread-pool) health servicer, listed in the policy."""
    async with grpc.aio.insecure_channel(server) as channel:
        stub = health_pb2_grpc.HealthStub(channel)
        request = health_pb2.HealthCheckRequest()
        response = await stub.Check(request, metadata=_md(CONTROL))
        assert response.status == health_pb2.HealthCheckResponse.SERVING
        with pytest.raises(grpc.aio.AioRpcError) as refused:
            await stub.Check(request, metadata=_md(PLATFORM))
        assert refused.value.code() is grpc.StatusCode.UNAUTHENTICATED


async def _reflect(stub, token):  # type: ignore[no-untyped-def]
    async def requests():  # type: ignore[no-untyped-def]
        yield reflection_pb2.ServerReflectionRequest(list_services="")

    call = stub.ServerReflectionInfo(requests(), metadata=_md(token), timeout=5)
    try:
        async for response in call:
            return sorted(s.name for s in response.list_services_response.service)
    except grpc.aio.AioRpcError as error:
        return error.code()
    return None


@pytest.mark.asyncio
async def test_a_plain_bidirectional_servicer_over_the_wire(server: str) -> None:
    async with grpc.aio.insecure_channel(server) as channel:
        stub = reflection_pb2_grpc.ServerReflectionStub(channel)
        assert _PROBE in await _reflect(stub, CONTROL)
        assert await _reflect(stub, NETWORK) is grpc.StatusCode.PERMISSION_DENIED
        assert await _reflect(stub, None) is grpc.StatusCode.UNAUTHENTICATED


@pytest.mark.asyncio
async def test_a_policy_naming_a_method_the_server_lacks_refuses_to_start() -> None:
    policy = CallerPolicy(
        {"/grpc.health.v1.Health/Chek": ["control-service"]}, _callers()
    )
    startup, shutdown = build_grpc_lifecycle(
        service_name="authz-typo",
        port=_free_port(),
        internal_token=PLATFORM,
        caller_policy=policy,
    )
    with pytest.raises(CallerPolicyError, match="no method Chek"):
        await startup()
    await shutdown()


def test_caller_secrets_load_from_the_environment() -> None:
    from service_toolkit.settings.config import InternalSettings

    settings = InternalSettings.load(
        prefix="INTERNAL_",
        env={"INTERNAL_CALLER_TOKENS": '{"control-service": "s1"}'},
        env_file=None,
    )
    callers = CallerCredentials(settings.caller_tokens)
    assert callers.resolve("s1") == "control-service"
    assert (
        InternalSettings.load(prefix="INTERNAL_", env={}, env_file=None).caller_tokens
        == {}
    )


def test_no_internal_secret_appears_when_the_settings_are_printed() -> None:
    import pprint

    from service_toolkit.settings.config import InternalSettings

    settings = InternalSettings(
        token="platform-secret", caller_tokens={"control-service": "caller-secret"}
    )
    from msgspec import Struct

    class ServiceSettings(Struct):
        internal: InternalSettings

    shown = [
        repr(ServiceSettings(internal=settings)),
        repr(settings),
        str(settings),
        f"{settings!r}",
        repr([settings]),
        repr({"internal": settings}),
        pprint.pformat(settings),
        repr(list(settings.__rich_repr__())),
    ]
    for text in shown:
        assert "platform-secret" not in text and "caller-secret" not in text, text
        assert "control-service" in text
    assert "<set>" in repr(settings)
    assert repr(InternalSettings()) == "InternalSettings(token=None, caller_tokens=[])"
    # Redaction is for display only: the values are still there to use.
    assert settings.token == "platform-secret"
    assert settings.caller_tokens == {"control-service": "caller-secret"}
