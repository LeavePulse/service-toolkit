"""gRPC server lifecycle helpers."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import grpc
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from grpc_reflection.v1alpha import reflection

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

logger = logging.getLogger(__name__)

_SERVER: grpc.aio.Server | None = None


def create_grpc_server(
    *,
    port: int = 50051,
    interceptors: Sequence[grpc.aio.ServerInterceptor] = (),
    reflection_enabled: bool = True,
    service_names: Sequence[str] = (),
) -> grpc.aio.Server:
    """Create a gRPC async server (not yet started).

    Returns the server so callers can register servicers before starting.
    """
    global _SERVER  # noqa: PLW0603

    server = grpc.aio.server(
        interceptors=list(interceptors),
        options=[
            ("grpc.max_receive_message_length", 16 * 1024 * 1024),
            ("grpc.max_send_message_length", 16 * 1024 * 1024),
        ],
    )
    server.add_insecure_port(f"[::]:{port}")

    # Health check
    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    health_servicer.set(
        "", health_pb2.HealthCheckResponse.SERVING,
    )
    for name in service_names:
        health_servicer.set(
            name, health_pb2.HealthCheckResponse.SERVING,
        )

    # Reflection
    if reflection_enabled:
        service_names_for_reflection = [
            health_pb2.DESCRIPTOR.services_by_name["Health"].full_name,
            *service_names,
            reflection.SERVICE_NAME,
        ]
        reflection.enable_server_reflection(service_names_for_reflection, server)

    _SERVER = server
    logger.info("Created gRPC server on port %d", port)
    return server


async def start_grpc_server(server: grpc.aio.Server | None = None) -> None:
    """Start the gRPC server."""
    srv = server or _SERVER
    if srv is None:
        msg = "No gRPC server created. Call create_grpc_server() first."
        raise RuntimeError(msg)
    await srv.start()
    logger.info("gRPC server started")


async def stop_grpc_server(
    server: grpc.aio.Server | None = None,
    *,
    grace: float = 5.0,
) -> None:
    """Gracefully stop the gRPC server."""
    global _SERVER  # noqa: PLW0603
    srv = server or _SERVER
    if srv is None:
        return
    await srv.stop(grace=grace)
    _SERVER = None
    logger.info("gRPC server stopped")


ServiceRegistrar = Callable[[grpc.aio.Server], None]


def build_grpc_lifecycle(
    *,
    service_name: str,
    port: int,
    internal_token: str | None = None,
    reflection_enabled: bool = True,
    service_names: Sequence[str] = (),
    registrars: Sequence[ServiceRegistrar] = (),
) -> tuple[Callable[[], object], Callable[[], object]]:
    """Build startup/shutdown callables for Litestar ``on_startup``/``on_shutdown``.

    This is the gRPC counterpart of ``create_service_app()`` — it eliminates
    the duplicated setup.py boilerplate across services.

    *registrars* are callables that receive the ``grpc.aio.Server`` and should
    call ``add_<X>Servicer_to_server(servicer, server)``.

    Returns ``(startup, shutdown)`` async callables.
    """

    async def _startup() -> None:
        interceptors: list[grpc.aio.ServerInterceptor] = []
        if internal_token:
            from .interceptors import InternalTokenInterceptor

            interceptors.append(InternalTokenInterceptor(internal_token))

        server = create_grpc_server(
            port=port,
            interceptors=interceptors,
            reflection_enabled=reflection_enabled,
            service_names=service_names,
        )

        for registrar in registrars:
            registrar(server)

        await start_grpc_server(server)
        logger.info("%s gRPC server started on port %d", service_name, port)

    async def _shutdown() -> None:
        await stop_grpc_server()
        logger.info("%s gRPC server stopped", service_name)

    return _startup, _shutdown


__all__ = [
    "build_grpc_lifecycle",
    "create_grpc_server",
    "start_grpc_server",
    "stop_grpc_server",
]
