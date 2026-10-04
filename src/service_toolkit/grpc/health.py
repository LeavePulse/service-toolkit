"""gRPC health: the service every server built here carries, and a client check.

Owned here rather than taken from grpcio-health-checking because the asyncio
servicer there keeps one shared Condition per service and deletes it when any
one watcher of that service ends (still so in 1.84). A second watcher of the
same service then waits on a Condition nothing notifies, and misses every
later change, NOT_SERVING at shutdown included. Watch is a stream of state
changes for every caller (https://grpc.github.io/grpc/python/grpc_health_checking.html),
so each watcher here has its own registration, cleaned up on its own.

Built on the generated health protobuf API only. Every method runs on the
server's event loop and none awaits while it changes state, so a status
change and a watcher arriving or leaving never interleave mid-update.
"""

from __future__ import annotations

import asyncio
from typing import Any

import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc

SERVING = health_pb2.HealthCheckResponse.SERVING
NOT_SERVING = health_pb2.HealthCheckResponse.NOT_SERVING
SERVICE_UNKNOWN = health_pb2.HealthCheckResponse.SERVICE_UNKNOWN


# Statuses are HealthCheckResponse.ServingStatus values: protobuf enums are
# ints at runtime, and annotated as such.


class _Watcher:
    """One Watch call: the latest status it has to see, and a wake-up.

    Holding only the latest status coalesces a burst of changes into the one
    the watcher ends up in, so a slow reader costs a constant amount.
    """

    __slots__ = ("changed", "status")

    def __init__(self, status: int) -> None:
        self.status = status
        self.changed = asyncio.Event()
        self.changed.set()


class HealthServicer(health_pb2_grpc.HealthServicer):
    """``grpc.health.v1.Health``: Check and Watch, for every caller.

    ``""`` (the server as a whole) is SERVING from construction; named
    services are reported once :meth:`set`. Call every method on the server's
    event loop.
    """

    def __init__(self) -> None:
        self._status: dict[str, int] = {"": SERVING}
        self._watchers: dict[str, set[_Watcher]] = {}
        self._shutting_down = False

    async def Check(  # noqa: N802 - the gRPC method name
        self, request: health_pb2.HealthCheckRequest, context: Any
    ) -> health_pb2.HealthCheckResponse:
        status = self._status.get(request.service)
        if status is None:
            await context.abort(grpc.StatusCode.NOT_FOUND, "unknown service")
        return health_pb2.HealthCheckResponse(status=status)

    async def Watch(  # noqa: N802 - the gRPC method name
        self, request: health_pb2.HealthCheckRequest, context: Any
    ) -> None:
        service = request.service
        watcher = _Watcher(self._status.get(service, SERVICE_UNKNOWN))
        self._watchers.setdefault(service, set()).add(watcher)
        try:
            sent: int | None = None
            while True:
                await watcher.changed.wait()
                watcher.changed.clear()
                status = watcher.status
                if status != sent:
                    await context.write(health_pb2.HealthCheckResponse(status=status))
                    sent = status
        finally:
            # This watcher's registration only: every other watcher of the
            # service keeps its own.
            watchers = self._watchers.get(service)
            if watchers is not None:
                watchers.discard(watcher)
                if not watchers:
                    del self._watchers[service]

    def watching(self, service: str) -> int:
        """How many Watch calls of *service* are live: for status and tests."""
        return len(self._watchers.get(service, ()))

    def set(self, service: str, status: int) -> None:
        """Report *service* as *status* and tell every live watcher of it.
        Ignored once shutdown has begun: NOT_SERVING is then final."""
        if self._shutting_down:
            return
        self._set(service, status)

    def enter_graceful_shutdown(self) -> None:
        """Report every known service NOT_SERVING, for good. Idempotent."""
        if self._shutting_down:
            return
        self._shutting_down = True
        for service in list(self._status):
            self._set(service, NOT_SERVING)

    def _set(self, service: str, status: int) -> None:
        self._status[service] = status
        for watcher in self._watchers.get(service, ()):
            watcher.status = status
            watcher.changed.set()


async def check_health(target: str, *, timeout: float = 5.0) -> bool:
    """Check if a gRPC service is healthy.

    Returns True if the service responds with SERVING status.
    """
    try:
        async with grpc.aio.insecure_channel(target) as channel:
            stub = health_pb2_grpc.HealthStub(channel)
            response = await stub.Check(
                health_pb2.HealthCheckRequest(service=""),
                timeout=timeout,
            )
            return response.status == health_pb2.HealthCheckResponse.SERVING
    except grpc.aio.AioRpcError:
        return False


__all__ = [
    "NOT_SERVING",
    "SERVICE_UNKNOWN",
    "SERVING",
    "HealthServicer",
    "check_health",
]
