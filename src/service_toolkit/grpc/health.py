"""gRPC health check utilities."""

from __future__ import annotations

import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc


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


__all__ = ["check_health"]
