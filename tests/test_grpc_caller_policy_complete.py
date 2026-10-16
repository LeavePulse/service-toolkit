"""A complete caller policy: every served method decided per caller.

The rules pinned here, over real connections where a client's view matters:

* the caller is named only by its own credential, never by metadata it sends;
* a complete policy decides every method: an unlisted one is denied, and
  startup refuses a policy that misses a served method or names one that is
  not served (only grpc health and reflection are exempt, by name);
* each entry is ENFORCE or OBSERVE(until): under OBSERVE the shared platform
  token is admitted for that method alone, logged; after ``until`` it is
  ENFORCE; a critical entry is never OBSERVE and no grace outlives the table's
  declared date by more than seven days;
* DEBUG (``require_internal_token=False``) never opens a complete policy, and
  one without any caller credential refuses to start.
"""

from __future__ import annotations

import logging
import socket
from datetime import UTC, date, datetime, timedelta

import grpc
import pytest
import pytest_asyncio
from grpc_health.v1 import health_pb2, health_pb2_grpc

from service_toolkit.grpc.authorization import (
    ENFORCE,
    CallerCredentials,
    CallerIdentity,
    CallerPolicy,
    CallerPolicyError,
    Observe,
    Rule,
    Verdict,
    current_caller,
)
from service_toolkit.grpc.interceptors import InternalTokenInterceptor
from service_toolkit.grpc.server import build_grpc_lifecycle

PLATFORM = "platform-token"
CONTROL = "control-secret"
NETWORK = "network-secret"

_PACKAGE = "authz.complete.v1"
_SERVICE = f"{_PACKAGE}.Probe"
_METHODS = ("Read", "Write", "Grace", "Expired", "Nobody")
READ, WRITE, GRACE, EXPIRED, NOBODY = (f"/{_SERVICE}/{m}" for m in _METHODS)
#: Registered on the server but not named in service_names: startup cannot see
#: it, so only the runtime deny keeps it shut.
_HIDDEN = f"{_PACKAGE}.Hidden"
HIDDEN = f"/{_HIDDEN}/Call"

TODAY = datetime.now(UTC).date()


def _callers() -> CallerCredentials:
    return CallerCredentials({"control-service": CONTROL, "network-service": NETWORK})


def _declare() -> None:
    from google.protobuf import descriptor_pb2, descriptor_pool, wrappers_pb2

    pool = descriptor_pool.Default()
    try:
        pool.FindServiceByName(_SERVICE)
        return
    except KeyError:
        pass
    pool.FindFileByName(wrappers_pb2.DESCRIPTOR.name)
    proto = descriptor_pb2.FileDescriptorProto(
        name="authz_complete_probe.proto",
        package=_PACKAGE,
        dependency=[wrappers_pb2.DESCRIPTOR.name],
    )
    probe = proto.service.add(name="Probe")
    for name in _METHODS:
        probe.method.add(
            name=name,
            input_type=".google.protobuf.StringValue",
            output_type=".google.protobuf.StringValue",
        )
    hidden = proto.service.add(name="Hidden")
    hidden.method.add(
        name="Call",
        input_type=".google.protobuf.StringValue",
        output_type=".google.protobuf.StringValue",
    )
    pool.Add(proto)


def _register(server: grpc.aio.Server) -> None:
    from google.protobuf.wrappers_pb2 import StringValue

    async def answer(_request, _context):  # type: ignore[no-untyped-def]
        caller = current_caller.get()
        return StringValue(value=caller.name if caller else "<shared>")

    codec = {
        "request_deserializer": StringValue.FromString,
        "response_serializer": StringValue.SerializeToString,
    }
    handler = grpc.unary_unary_rpc_method_handler(answer, **codec)
    server.add_generic_rpc_handlers(
        (
            grpc.method_handlers_generic_handler(
                _SERVICE, dict.fromkeys(_METHODS, handler)
            ),
            grpc.method_handlers_generic_handler(_HIDDEN, {"Call": handler}),
        )
    )


def _rules() -> dict[str, Rule]:
    return {
        READ: Rule(frozenset({"control-service", "network-service"})),
        WRITE: Rule(frozenset({"control-service"}), critical=True),
        GRACE: Rule(frozenset({"control-service"}), Observe(TODAY + timedelta(days=6))),
        EXPIRED: Rule(
            frozenset({"control-service"}), Observe(TODAY - timedelta(days=1))
        ),
        NOBODY: Rule(frozenset(), critical=True),
    }


def _complete(rules: dict[str, Rule] | None = None, **kwargs) -> CallerPolicy:  # type: ignore[no-untyped-def]
    return CallerPolicy(
        rules if rules is not None else _rules(),
        kwargs.pop("callers", _callers()),
        complete=True,
        declared_on=kwargs.pop("declared_on", TODAY - timedelta(days=1)),
        **kwargs,
    )


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


async def _serve(policy: CallerPolicy, **kwargs):  # type: ignore[no-untyped-def]
    _declare()
    port = _free_port()
    startup, shutdown = build_grpc_lifecycle(
        service_name="authz-complete",
        port=port,
        service_names=kwargs.pop("service_names", [_SERVICE]),
        registrars=[_register],
        caller_policy=policy,
        **kwargs,
    )
    await startup()
    return f"127.0.0.1:{port}", shutdown


@pytest_asyncio.fixture
async def server():  # type: ignore[no-untyped-def]
    address, shutdown = await _serve(_complete(), internal_token=PLATFORM)
    try:
        yield address
    finally:
        await shutdown()


async def _call(
    address: str, method: str, metadata: list[tuple[str, str]]
) -> str | grpc.StatusCode:
    from google.protobuf.wrappers_pb2 import StringValue

    async with grpc.aio.insecure_channel(address) as channel:
        stub = channel.unary_unary(
            method,
            request_serializer=StringValue.SerializeToString,
            response_deserializer=StringValue.FromString,
        )
        try:
            return (
                await stub(StringValue(value="x"), metadata=metadata, timeout=5)
            ).value
        except grpc.aio.AioRpcError as error:
            return error.code()


def _token(secret: str) -> list[tuple[str, str]]:
    return [("x-internal-token", secret)]


# --- identity ---------------------------------------------------------------


def test_a_secret_resolves_to_a_frozen_identity() -> None:
    identity = _callers().resolve(CONTROL)
    assert identity == CallerIdentity("control-service")
    with pytest.raises(AttributeError):
        identity.name = "network-service"  # type: ignore[misc,union-attr]
    assert _callers().resolve("x-caller") is None


@pytest.mark.asyncio
async def test_spoofed_caller_metadata_changes_nothing(server: str) -> None:
    for header in ("x-caller", "x-caller-name", "x-service-name"):
        spoofed = [(header, "control-service")]
        # The label is the credential's, whatever the header claims.
        assert await _call(server, READ, _token(NETWORK) + spoofed) == "network-service"
        assert (
            await _call(server, WRITE, _token(NETWORK) + spoofed)
            is grpc.StatusCode.PERMISSION_DENIED
        )
        assert await _call(server, WRITE, spoofed) is grpc.StatusCode.UNAUTHENTICATED


# --- the decision over a real connection --------------------------------------


@pytest.mark.asyncio
async def test_the_callers_own_credential_decides(server: str) -> None:
    assert await _call(server, WRITE, _token(CONTROL)) == "control-service"
    assert (
        await _call(server, WRITE, _token(NETWORK)) is grpc.StatusCode.PERMISSION_DENIED
    )
    assert (
        await _call(server, WRITE, _token("unknown")) is grpc.StatusCode.UNAUTHENTICATED
    )
    assert await _call(server, WRITE, []) is grpc.StatusCode.UNAUTHENTICATED


@pytest.mark.asyncio
async def test_enforce_denies_the_shared_token(server: str) -> None:
    for method in (READ, WRITE, NOBODY):
        assert (
            await _call(server, method, _token(PLATFORM))
            is grpc.StatusCode.UNAUTHENTICATED
        )


@pytest.mark.asyncio
async def test_a_method_admitting_nobody_refuses_every_caller(server: str) -> None:
    assert (
        await _call(server, NOBODY, _token(CONTROL))
        is grpc.StatusCode.PERMISSION_DENIED
    )
    assert (
        await _call(server, NOBODY, _token(NETWORK))
        is grpc.StatusCode.PERMISSION_DENIED
    )


@pytest.mark.asyncio
async def test_observe_admits_the_shared_token_for_that_method_and_logs_it(
    server: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="service_toolkit.grpc.authorization"):
        assert await _call(server, GRACE, _token(PLATFORM)) == "<shared>"
    [record] = [r for r in caplog.records if "shared platform token" in r.getMessage()]
    assert record.levelno == logging.WARNING
    assert GRACE in record.getMessage()
    assert "control-service" in record.getMessage()
    assert PLATFORM not in record.getMessage()


@pytest.mark.asyncio
async def test_observe_still_decides_a_caller_credential_by_the_entry(
    server: str,
) -> None:
    assert await _call(server, GRACE, _token(CONTROL)) == "control-service"
    assert (
        await _call(server, GRACE, _token(NETWORK)) is grpc.StatusCode.PERMISSION_DENIED
    )
    assert (
        await _call(server, GRACE, _token("unknown")) is grpc.StatusCode.UNAUTHENTICATED
    )


@pytest.mark.asyncio
async def test_an_expired_observe_is_enforce(server: str) -> None:
    assert (
        await _call(server, EXPIRED, _token(PLATFORM))
        is grpc.StatusCode.UNAUTHENTICATED
    )
    assert await _call(server, EXPIRED, _token(CONTROL)) == "control-service"


@pytest.mark.asyncio
async def test_an_unlisted_method_is_denied(server: str) -> None:
    for metadata in (_token(CONTROL), _token(PLATFORM), []):
        assert (
            await _call(server, HIDDEN, metadata) is grpc.StatusCode.PERMISSION_DENIED
        )


@pytest.mark.asyncio
async def test_health_is_exempt_by_name(server: str) -> None:
    async with grpc.aio.insecure_channel(server) as channel:
        response = await health_pb2_grpc.HealthStub(channel).Check(
            health_pb2.HealthCheckRequest(), timeout=5
        )
    assert response.status == health_pb2.HealthCheckResponse.SERVING


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [None, PLATFORM])
async def test_debug_never_opens_a_complete_policy(token: str | None) -> None:
    address, shutdown = await _serve(
        _complete(), internal_token=token, require_internal_token=False
    )
    try:
        assert await _call(address, WRITE, []) is grpc.StatusCode.UNAUTHENTICATED
        assert (
            await _call(address, WRITE, _token(PLATFORM))
            is grpc.StatusCode.UNAUTHENTICATED
        )
        assert (
            await _call(address, WRITE, _token(NETWORK))
            is grpc.StatusCode.PERMISSION_DENIED
        )
        assert await _call(address, HIDDEN, []) is grpc.StatusCode.PERMISSION_DENIED
        assert await _call(address, WRITE, _token(CONTROL)) == "control-service"
    finally:
        await shutdown()


@pytest.mark.asyncio
async def test_without_a_platform_token_observe_admits_nothing_shared() -> None:
    address, shutdown = await _serve(
        _complete(), internal_token=None, require_internal_token=False
    )
    try:
        assert (
            await _call(address, GRACE, _token(PLATFORM))
            is grpc.StatusCode.UNAUTHENTICATED
        )
    finally:
        await shutdown()


def test_a_complete_policy_without_credentials_refuses_to_build() -> None:
    policy = _complete(
        {NOBODY: Rule(frozenset(), critical=True)}, callers=CallerCredentials({})
    )
    with pytest.raises(RuntimeError, match="no caller credential"):
        build_grpc_lifecycle(
            service_name="authz-empty",
            port=_free_port(),
            internal_token=PLATFORM,
            require_internal_token=False,
            caller_policy=policy,
        )


def test_the_platform_token_may_not_double_as_a_caller_secret() -> None:
    with pytest.raises(ValueError, match="platform token"):
        InternalTokenInterceptor(CONTROL, policy=_complete())


def test_a_complete_policy_takes_no_exemption_or_alternate_verifier() -> None:
    async def verifier(_method: str, _token: str):  # type: ignore[no-untyped-def]
        return None

    with pytest.raises(ValueError, match="complete"):
        InternalTokenInterceptor(
            PLATFORM, exempt_methods=["Probe/Read"], policy=_complete()
        )
    with pytest.raises(ValueError, match="complete"):
        InternalTokenInterceptor(
            PLATFORM, alternate_verifier=verifier, policy=_complete()
        )


# --- startup: both directions ------------------------------------------------


async def _refuses_to_start(policy: CallerPolicy, fragment: str) -> None:
    from service_toolkit.grpc.server import stop_grpc_server

    try:
        with pytest.raises(CallerPolicyError, match=fragment):
            await _serve(policy, internal_token=PLATFORM)
    finally:
        # Stops the server the failed startup created, or one that wrongly
        # started, so a regression fails instead of hanging the run.
        await stop_grpc_server(grace=0)


@pytest.mark.asyncio
async def test_a_served_method_missing_from_the_policy_refuses_to_start() -> None:
    rules = _rules()
    del rules[NOBODY]
    await _refuses_to_start(_complete(rules), "Nobody")


@pytest.mark.asyncio
async def test_a_listed_method_that_is_not_served_refuses_to_start() -> None:
    rules = _rules()
    rules[HIDDEN] = Rule(frozenset({"control-service"}))
    await _refuses_to_start(_complete(rules), "not served")


def test_check_served_both_directions_directly() -> None:
    _declare()
    policy = _complete()
    policy.check_served([_SERVICE, "grpc.health.v1.Health"])
    with pytest.raises(CallerPolicyError, match="Hidden/Call"):
        policy.check_served([_SERVICE, _HIDDEN])
    partial = _complete({READ: Rule(frozenset({"control-service"}))})
    with pytest.raises(CallerPolicyError, match="not listed"):
        partial.check_served([_SERVICE])


def test_only_health_and_reflection_are_exempt_and_only_by_name() -> None:
    _declare()
    # A service merely containing "grpc.health" in its name is not exempt.
    policy = _complete()
    assert not policy.exempt("/my.grpc.health.v1.Health/Check")
    assert not policy.exempt("/pkg.grpc.reflection.X/Y")
    assert policy.exempt("/grpc.health.v1.Health/Check")
    assert policy.exempt("/grpc.health.v1.Health/Watch")
    assert policy.exempt(
        "/grpc.reflection.v1alpha.ServerReflection/ServerReflectionInfo"
    )


# --- load-time refusals ---------------------------------------------------------


def test_a_critical_entry_cannot_observe() -> None:
    with pytest.raises(CallerPolicyError, match="critical"):
        _complete(
            {WRITE: Rule(frozenset({"control-service"}), Observe(TODAY), critical=True)}
        )


@pytest.mark.parametrize("days", [8, 30])
def test_a_grace_longer_than_seven_days_is_refused(days: int) -> None:
    declared = date(2026, 10, 1)
    with pytest.raises(CallerPolicyError, match="7 days"):
        _complete(
            {
                READ: Rule(
                    frozenset({"control-service"}),
                    Observe(declared + timedelta(days=days)),
                )
            },
            declared_on=declared,
        )


def test_seven_days_is_the_longest_grace() -> None:
    declared = date(2026, 10, 1)
    _complete(
        {
            READ: Rule(
                frozenset({"control-service"}), Observe(declared + timedelta(days=7))
            )
        },
        declared_on=declared,
    )


def test_observe_needs_a_declared_date_and_cannot_end_before_it() -> None:
    rule = Rule(frozenset({"control-service"}), Observe(TODAY))
    with pytest.raises(CallerPolicyError, match="declared_on"):
        CallerPolicy({READ: rule}, _callers(), complete=True)
    with pytest.raises(CallerPolicyError, match="before"):
        _complete({READ: rule}, declared_on=TODAY + timedelta(days=1))


def test_the_decision_follows_the_clock_without_a_restart() -> None:
    declared = date(2026, 10, 1)
    until = declared + timedelta(days=7)
    clock = [until]
    policy = _complete(
        {READ: Rule(frozenset({"control-service"}), Observe(until))},
        declared_on=declared,
        today=lambda: clock[0],
    )
    assert (
        policy.decide(READ, PLATFORM, shared_token=PLATFORM).verdict
        is Verdict.ADMIT_SHARED
    )
    clock[0] = until + timedelta(days=1)
    assert (
        policy.decide(READ, PLATFORM, shared_token=PLATFORM).verdict
        is Verdict.UNAUTHENTICATED
    )
    assert policy.decide(READ, CONTROL, shared_token=PLATFORM).verdict is Verdict.ADMIT


def test_an_enforce_rule_is_the_default_state() -> None:
    assert Rule(frozenset({"control-service"})).state is ENFORCE


def test_an_empty_caller_set_is_only_for_a_complete_policy() -> None:
    with pytest.raises(CallerPolicyError, match="admits no caller"):
        CallerPolicy({READ: Rule(frozenset())}, _callers())
