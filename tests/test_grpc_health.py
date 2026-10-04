"""The health servicer: Check and Watch for every caller, over real connections.

Each test serves one HealthServicer it holds on a real grpc.aio server, so
status changes are driven through the servicer's own API and every outcome
is read as a client reads it.
"""

from __future__ import annotations

import asyncio
import socket

import grpc
import pytest
import pytest_asyncio
from grpc_health.v1 import health_pb2, health_pb2_grpc

from service_toolkit.grpc.authorization import CallerCredentials, CallerPolicy
from service_toolkit.grpc.health import (
    NOT_SERVING,
    SERVICE_UNKNOWN,
    SERVING,
    HealthServicer,
)
from service_toolkit.grpc.interceptors import InternalTokenInterceptor

SERVICE = "pkg.v1.Thing"
CONTROL = "control-secret"


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest_asyncio.fixture
async def served():  # type: ignore[no-untyped-def]
    """A servicer, and a channel to the server carrying it. Watch is listed in
    a caller policy, so a refused Watch can be shown never to register."""
    servicer = HealthServicer()
    servicer.set(SERVICE, SERVING)
    policy = CallerPolicy(
        {"/grpc.health.v1.Health/Watch": ["control-service"]},
        CallerCredentials({"control-service": CONTROL}),
    )
    server = grpc.aio.server(
        interceptors=[InternalTokenInterceptor("platform", policy=policy)]
    )
    health_pb2_grpc.add_HealthServicer_to_server(servicer, server)
    port = server.add_insecure_port(f"127.0.0.1:{_free_port()}")
    await server.start()
    channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")
    try:
        yield servicer, health_pb2_grpc.HealthStub(channel)
    finally:
        await channel.close()
        await server.stop(None)


def _watch(stub, service: str = SERVICE):  # type: ignore[no-untyped-def]
    return stub.Watch(
        health_pb2.HealthCheckRequest(service=service),
        metadata=[("x-internal-token", CONTROL)],
    )


async def _next(watch) -> int:  # type: ignore[no-untyped-def]
    return (await asyncio.wait_for(watch.read(), timeout=5)).status


async def _until(predicate) -> None:  # type: ignore[no-untyped-def]
    for _ in range(500):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not reached")


async def _cancel(watch) -> None:  # type: ignore[no-untyped-def]
    watch.cancel()
    with pytest.raises(asyncio.CancelledError):
        await watch.read()


@pytest.mark.asyncio
async def test_check(served) -> None:  # type: ignore[no-untyped-def]
    _, stub = served
    for service in ("", SERVICE):
        response = await stub.Check(health_pb2.HealthCheckRequest(service=service))
        assert response.status == SERVING
    with pytest.raises(grpc.aio.AioRpcError) as unknown:
        await stub.Check(health_pb2.HealthCheckRequest(service="no.such.Service"))
    assert unknown.value.code() is grpc.StatusCode.NOT_FOUND


@pytest.mark.asyncio
async def test_watching_an_unknown_service_says_so_then_follows_it(served) -> None:  # type: ignore[no-untyped-def]
    servicer, stub = served
    watch = _watch(stub, "later.v1.Service")
    assert await _next(watch) == SERVICE_UNKNOWN
    servicer.set("later.v1.Service", SERVING)
    assert await _next(watch) == SERVING
    await _cancel(watch)


@pytest.mark.asyncio
async def test_every_watcher_sees_every_settled_change(served) -> None:  # type: ignore[no-untyped-def]
    servicer, stub = served
    watches = [_watch(stub) for _ in range(3)]
    for watch in watches:
        assert await _next(watch) == SERVING
    servicer.set(SERVICE, NOT_SERVING)
    for watch in watches:
        assert await _next(watch) == NOT_SERVING
    servicer.set(SERVICE, SERVING)
    for watch in watches:
        assert await _next(watch) == SERVING
    for watch in watches:
        await _cancel(watch)
    await _until(lambda: servicer.watching(SERVICE) == 0)


@pytest.mark.asyncio
async def test_one_watcher_leaving_never_silences_another(served) -> None:  # type: ignore[no-untyped-def]
    """The defect this servicer exists for: grpc_health's asyncio servicer
    deletes the service's shared Condition when any watcher ends, and the
    one still watching is never told again."""
    servicer, stub = served
    staying, leaving = _watch(stub), _watch(stub)
    assert await _next(staying) == SERVING
    assert await _next(leaving) == SERVING
    await _cancel(leaving)
    await _until(lambda: servicer.watching(SERVICE) == 1)
    servicer.enter_graceful_shutdown()
    assert await _next(staying) == NOT_SERVING
    await _cancel(staying)


@pytest.mark.asyncio
async def test_watchers_leaving_while_the_status_changes_never_take_another_with_them(
    served,
) -> None:  # type: ignore[no-untyped-def]
    servicer, stub = served
    staying = _watch(stub)
    assert await _next(staying) == SERVING
    statuses = (NOT_SERVING, SERVING)
    for round_ in range(40):
        leaving = _watch(stub)
        await _next(leaving)
        # The departure and the change race on the server's loop.
        cancelled = asyncio.create_task(_cancel(leaving))
        servicer.set(SERVICE, statuses[round_ % 2])
        await cancelled
    await _until(lambda: servicer.watching(SERVICE) == 1)
    # Whatever the survivor coalesced in between, it ends where the service is.
    servicer.set(SERVICE, SERVING)
    servicer.set(SERVICE, NOT_SERVING)
    seen = None
    while seen != NOT_SERVING:
        seen = await _next(staying)
    await _cancel(staying)


@pytest.mark.asyncio
async def test_shutdown_is_final_and_happens_once(served) -> None:  # type: ignore[no-untyped-def]
    servicer, stub = served
    watch = _watch(stub)
    assert await _next(watch) == SERVING
    servicer.enter_graceful_shutdown()
    servicer.enter_graceful_shutdown()
    assert await _next(watch) == NOT_SERVING
    servicer.set(SERVICE, SERVING)  # ignored from now on
    for service in ("", SERVICE):
        response = await stub.Check(health_pb2.HealthCheckRequest(service=service))
        assert response.status == NOT_SERVING
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(watch.read(), timeout=0.3)
    await _cancel(watch)


@pytest.mark.asyncio
async def test_a_refused_watch_never_registers(served) -> None:  # type: ignore[no-untyped-def]
    servicer, stub = served
    refused = stub.Watch(
        health_pb2.HealthCheckRequest(service=SERVICE),
        metadata=[("x-internal-token", "platform")],
    )
    with pytest.raises(grpc.aio.AioRpcError) as error:
        await asyncio.wait_for(refused.read(), timeout=5)
    assert error.value.code() is grpc.StatusCode.UNAUTHENTICATED
    assert servicer.watching(SERVICE) == 0
