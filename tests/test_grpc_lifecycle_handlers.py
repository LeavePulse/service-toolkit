"""The complete gRPC lifecycle, over real connections, for every handler shape.

`build_grpc_lifecycle` installs the health service and the domain-error
interceptor on every server, so both have to be right for every handler the
servers carry: Health's long-lived Watch as well as Check, and domain errors
from unary and streaming servicers alike, whether a servicer is a coroutine,
an async generator, a reader/writer coroutine, or a plain function grpc runs
on its thread pool.
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator

import grpc
import pytest
import pytest_asyncio
from google.protobuf import descriptor_pb2, descriptor_pool, wrappers_pb2
from google.protobuf.wrappers_pb2 import StringValue
from grpc_health.v1 import health_pb2, health_pb2_grpc

from service_toolkit.grpc.authorization import CallerCredentials, CallerPolicy
from service_toolkit.grpc.server import build_grpc_lifecycle

PLATFORM = "platform-token"
CONTROL = "control-secret"
SHAPES = "lifecycle.test.v1.Shapes"


class ResourceNotFoundError(Exception):
    """Named as awesome-errors names it: maps to NOT_FOUND."""


#: Every servicer behaviour that ran, so a refusal can be shown to reach none.
RAN: list[str] = []


def _declare() -> None:
    pool = descriptor_pool.Default()
    try:
        pool.FindServiceByName(SHAPES)
        return
    except KeyError:
        pass
    pool.FindFileByName(wrappers_pb2.DESCRIPTOR.name)
    proto = descriptor_pb2.FileDescriptorProto(
        name="lifecycle_test_shapes.proto",
        package="lifecycle.test.v1",
        dependency=[wrappers_pb2.DESCRIPTOR.name],
    )
    service = proto.service.add(name="Shapes")
    for name, client, server in (
        ("PlainUnary", False, False),
        ("PlainUnaryFails", False, False),
        ("PlainStreamFails", False, True),
        ("GeneratorFails", False, True),
        ("WriterStream", False, True),
        ("ReaderCount", True, False),
        ("BidiFails", True, True),
    ):
        service.method.add(
            name=name,
            input_type=".google.protobuf.StringValue",
            output_type=".google.protobuf.StringValue",
            client_streaming=client,
            server_streaming=server,
        )
    pool.Add(proto)


def _register(server: grpc.aio.Server) -> None:
    def plain_unary(request, _context):  # type: ignore[no-untyped-def]
        RAN.append("PlainUnary")
        return StringValue(value=f"plain:{request.value}")

    def plain_unary_fails(_request, _context):  # type: ignore[no-untyped-def]
        RAN.append("PlainUnaryFails")
        raise ResourceNotFoundError("no such thing")

    def plain_stream_fails(_request, _context):  # type: ignore[no-untyped-def]
        RAN.append("PlainStreamFails")
        yield StringValue(value="first")
        raise ResourceNotFoundError("gone mid-stream")

    async def generator_fails(_request, _context) -> AsyncIterator[StringValue]:  # type: ignore[no-untyped-def]
        RAN.append("GeneratorFails")
        yield StringValue(value="first")
        raise ResourceNotFoundError("gone mid-stream")

    async def writer_stream(_request, context) -> None:  # type: ignore[no-untyped-def]
        RAN.append("WriterStream")
        for index in range(3):
            await context.write(StringValue(value=str(index)))

    async def reader_count(_requests, context):  # type: ignore[no-untyped-def]
        RAN.append("ReaderCount")
        count = 0
        while (await context.read()) is not grpc.aio.EOF:
            count += 1
        return StringValue(value=str(count))

    async def bidi_fails(requests, _context):  # type: ignore[no-untyped-def]
        RAN.append("BidiFails")
        async for request in requests:
            yield StringValue(value=request.value)
        raise ResourceNotFoundError("done and gone")

    codec = {
        "request_deserializer": StringValue.FromString,
        "response_serializer": StringValue.SerializeToString,
    }
    server.add_generic_rpc_handlers(
        (
            grpc.method_handlers_generic_handler(
                SHAPES,
                {
                    "PlainUnary": grpc.unary_unary_rpc_method_handler(
                        plain_unary, **codec
                    ),
                    "PlainUnaryFails": grpc.unary_unary_rpc_method_handler(
                        plain_unary_fails, **codec
                    ),
                    "PlainStreamFails": grpc.unary_stream_rpc_method_handler(
                        plain_stream_fails, **codec
                    ),
                    "GeneratorFails": grpc.unary_stream_rpc_method_handler(
                        generator_fails, **codec
                    ),
                    "WriterStream": grpc.unary_stream_rpc_method_handler(
                        writer_stream, **codec
                    ),
                    "ReaderCount": grpc.stream_unary_rpc_method_handler(
                        reader_count, **codec
                    ),
                    "BidiFails": grpc.stream_stream_rpc_method_handler(
                        bidi_fails, **codec
                    ),
                },
            ),
        )
    )


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest_asyncio.fixture
async def lifecycle():  # type: ignore[no-untyped-def]
    """A started server and its shutdown, as a service builds them."""
    _declare()
    RAN.clear()
    port = _free_port()
    startup, shutdown = build_grpc_lifecycle(
        service_name="lifecycle-test",
        port=port,
        internal_token=PLATFORM,
        service_names=[SHAPES],
        registrars=[_register],
        caller_policy=CallerPolicy(
            {"/grpc.health.v1.Health/Watch": ["control-service"]},
            CallerCredentials({"control-service": CONTROL}),
        ),
    )
    await startup()
    stopped = False

    async def stop() -> None:
        nonlocal stopped
        if not stopped:
            stopped = True
            await shutdown()

    try:
        yield f"127.0.0.1:{port}", stop
    finally:
        await stop()


def _md(token: str) -> list[tuple[str, str]]:
    return [("x-internal-token", token)]


def _call(channel: grpc.aio.Channel, method: str, kind: str):  # type: ignore[no-untyped-def]
    codec = {
        "request_serializer": StringValue.SerializeToString,
        "response_deserializer": StringValue.FromString,
    }
    return getattr(channel, kind)(f"/{SHAPES}/{method}", **codec)


def _served(method: str, code: str) -> float:
    from service_toolkit.grpc.metrics import _SERVER_REQUESTS_TOTAL

    for metric in _SERVER_REQUESTS_TOTAL.collect():
        for sample in metric.samples:
            labels = sample.labels
            if (
                sample.name.endswith("_total")
                and labels.get("service") == "lifecycle-test"
                and labels.get("grpc_method") == method.lower()
                and labels.get("grpc_code") == code.lower()
            ):
                return sample.value
    return 0.0


# --- health --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_answers_for_the_server_and_each_named_service(lifecycle) -> None:  # type: ignore[no-untyped-def]
    target, _ = lifecycle
    async with grpc.aio.insecure_channel(target) as channel:
        stub = health_pb2_grpc.HealthStub(channel)
        for service in ("", SHAPES):
            response = await stub.Check(
                health_pb2.HealthCheckRequest(service=service), metadata=_md(PLATFORM)
            )
            assert response.status == health_pb2.HealthCheckResponse.SERVING
        with pytest.raises(grpc.aio.AioRpcError) as unknown:
            await stub.Check(
                health_pb2.HealthCheckRequest(service="no.such.Service"),
                metadata=_md(PLATFORM),
            )
        assert unknown.value.code() is grpc.StatusCode.NOT_FOUND


@pytest.mark.asyncio
async def test_watch_answers_at_once_and_a_cancelled_watch_ends_cleanly(
    lifecycle,
) -> None:  # type: ignore[no-untyped-def]
    target, _ = lifecycle
    async with grpc.aio.insecure_channel(target) as channel:
        stub = health_pb2_grpc.HealthStub(channel)
        for _ in range(3):  # cancel repeatedly: nothing is left wedged
            watch = stub.Watch(
                health_pb2.HealthCheckRequest(service=SHAPES), metadata=_md(CONTROL)
            )
            first = await asyncio.wait_for(watch.read(), timeout=5)
            assert first.status == health_pb2.HealthCheckResponse.SERVING
            watch.cancel()
            with pytest.raises(asyncio.CancelledError):
                await watch.read()
            assert watch.cancelled()
        # The server still answers after the cancellations.
        response = await stub.Check(
            health_pb2.HealthCheckRequest(), metadata=_md(PLATFORM)
        )
        assert response.status == health_pb2.HealthCheckResponse.SERVING


@pytest.mark.asyncio
async def test_an_open_watch_is_told_not_serving_on_shutdown(lifecycle) -> None:  # type: ignore[no-untyped-def]
    target, stop = lifecycle
    async with grpc.aio.insecure_channel(target) as channel:
        stub = health_pb2_grpc.HealthStub(channel)
        watch = stub.Watch(
            health_pb2.HealthCheckRequest(service=""), metadata=_md(CONTROL)
        )
        first = await asyncio.wait_for(watch.read(), timeout=5)
        assert first.status == health_pb2.HealthCheckResponse.SERVING
        stopping = asyncio.create_task(stop())
        second = await asyncio.wait_for(watch.read(), timeout=5)
        assert second.status == health_pb2.HealthCheckResponse.NOT_SERVING
        watch.cancel()
        await stopping


@pytest.mark.asyncio
async def test_a_surviving_watcher_is_told_not_serving_on_shutdown(lifecycle) -> None:  # type: ignore[no-untyped-def]
    """Two watchers of one service; one leaves; the other still hears the end."""
    target, stop = lifecycle
    async with grpc.aio.insecure_channel(target) as channel:
        stub = health_pb2_grpc.HealthStub(channel)
        request = health_pb2.HealthCheckRequest(service=SHAPES)
        leaving = stub.Watch(request, metadata=_md(CONTROL))
        staying = stub.Watch(request, metadata=_md(CONTROL))
        for watch in (leaving, staying):
            first = await asyncio.wait_for(watch.read(), timeout=5)
            assert first.status == health_pb2.HealthCheckResponse.SERVING
        leaving.cancel()
        with pytest.raises(asyncio.CancelledError):
            await leaving.read()
        stopping = asyncio.create_task(stop())
        last = await asyncio.wait_for(staying.read(), timeout=5)
        assert last.status == health_pb2.HealthCheckResponse.NOT_SERVING
        staying.cancel()
        await stopping


# --- domain errors and execution models ------------------------------------------------


@pytest.mark.asyncio
async def test_a_plain_unary_servicer_runs_under_the_complete_lifecycle(
    lifecycle,
) -> None:  # type: ignore[no-untyped-def]
    target, _ = lifecycle
    async with grpc.aio.insecure_channel(target) as channel:
        call = _call(channel, "PlainUnary", "unary_unary")
        response = await call(StringValue(value="x"), metadata=_md(PLATFORM))
        assert response.value == "plain:x"
        with pytest.raises(grpc.aio.AioRpcError) as failed:
            await _call(channel, "PlainUnaryFails", "unary_unary")(
                StringValue(), metadata=_md(PLATFORM)
            )
        assert failed.value.code() is grpc.StatusCode.NOT_FOUND
        assert failed.value.details() == "no such thing"


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["PlainStreamFails", "GeneratorFails"])
async def test_a_domain_error_mid_stream_ends_with_its_status(
    lifecycle, method: str
) -> None:  # type: ignore[no-untyped-def]
    target, _ = lifecycle
    before = _served(method, "NOT_FOUND")
    async with grpc.aio.insecure_channel(target) as channel:
        call = _call(channel, method, "unary_stream")(
            StringValue(), metadata=_md(PLATFORM)
        )
        received: list[str] = []
        with pytest.raises(grpc.aio.AioRpcError) as failed:
            async for response in call:
                received.append(response.value)
        assert received == ["first"]
        assert failed.value.code() is grpc.StatusCode.NOT_FOUND
        assert failed.value.details() == "gone mid-stream"
    # Counted once, over the stream's life, with the status it ended on. The
    # server records it as the call finishes on its side, which can be just
    # after the client has read the status.
    for _ in range(100):
        if _served(method, "NOT_FOUND") > before:
            break
        await asyncio.sleep(0.01)
    assert _served(method, "NOT_FOUND") - before == 1
    assert _served(method, "OK") == 0


@pytest.mark.asyncio
async def test_writer_and_reader_style_servicers_keep_their_semantics(
    lifecycle,
) -> None:  # type: ignore[no-untyped-def]
    target, _ = lifecycle
    async with grpc.aio.insecure_channel(target) as channel:
        written = _call(channel, "WriterStream", "unary_stream")(
            StringValue(), metadata=_md(PLATFORM)
        )
        assert [r.value async for r in written] == ["0", "1", "2"]

        async def four() -> AsyncIterator[StringValue]:
            for _ in range(4):
                yield StringValue(value="r")

        counted = await _call(channel, "ReaderCount", "stream_unary")(
            four(), metadata=_md(PLATFORM)
        )
        assert counted.value == "4"


@pytest.mark.asyncio
async def test_a_bidirectional_domain_error_ends_with_its_status(lifecycle) -> None:  # type: ignore[no-untyped-def]
    target, _ = lifecycle
    async with grpc.aio.insecure_channel(target) as channel:

        async def two() -> AsyncIterator[StringValue]:
            for value in ("a", "b"):
                yield StringValue(value=value)

        call = _call(channel, "BidiFails", "stream_stream")(
            two(), metadata=_md(PLATFORM)
        )
        received: list[str] = []
        with pytest.raises(grpc.aio.AioRpcError) as failed:
            async for response in call:
                received.append(response.value)
        assert received == ["a", "b"]
        assert failed.value.code() is grpc.StatusCode.NOT_FOUND


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "kind"),
    [("PlainStreamFails", "unary_stream"), ("PlainUnaryFails", "unary_unary")],
)
async def test_a_plain_servicers_domain_error_never_leaves_the_call_hanging(
    lifecycle, method: str, kind: str
) -> None:  # type: ignore[no-untyped-def]
    """Ending a thread-pool streaming call with the context's abort left about
    a third of calls hanging until their deadline (grpc 1.81). Every call here
    must end with its status, well inside a short deadline."""
    target, _ = lifecycle
    before = _served(method, "NOT_FOUND")
    async with grpc.aio.insecure_channel(target) as channel:
        codes = []
        for _ in range(40):
            call = _call(channel, method, kind)(
                StringValue(), metadata=_md(PLATFORM), timeout=2
            )
            try:
                if kind == "unary_stream":
                    _ = [response async for response in call]
                else:
                    await call
            except grpc.aio.AioRpcError as error:
                codes.append((error.code(), error.details()))
    assert {code for code, _ in codes} == {grpc.StatusCode.NOT_FOUND}
    assert len(codes) == 40
    for _ in range(100):
        if _served(method, "NOT_FOUND") - before == 40:
            break
        await asyncio.sleep(0.01)
    assert _served(method, "NOT_FOUND") - before == 40
