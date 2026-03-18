"""Prometheus instrumentation for gRPC traffic."""

from __future__ import annotations

import time
from typing import Any

import grpc
from prometheus_client import Counter, Histogram

from ..observability.metrics import metric_label

_SERVER_REQUESTS_TOTAL = Counter(
    "leavepulse_grpc_server_requests_total",
    "Total unary gRPC requests handled by a service",
    ["service", "grpc_service", "grpc_method", "grpc_code"],
)
_SERVER_REQUEST_DURATION_SECONDS = Histogram(
    "leavepulse_grpc_server_request_duration_seconds",
    "Latency of unary gRPC requests handled by a service",
    ["service", "grpc_service", "grpc_method"],
)
_CLIENT_REQUESTS_TOTAL = Counter(
    "leavepulse_grpc_client_requests_total",
    "Total unary gRPC requests sent by a service",
    ["service", "target", "grpc_service", "grpc_method", "grpc_code"],
)
_CLIENT_REQUEST_DURATION_SECONDS = Histogram(
    "leavepulse_grpc_client_request_duration_seconds",
    "Latency of unary gRPC requests sent by a service",
    ["service", "target", "grpc_service", "grpc_method"],
)
_SYSTEM_METHOD_MARKERS = ("grpc.health", "grpc.reflection")


def _split_method(method: str | None) -> tuple[str, str]:
    normalized = str(method or "").strip().lstrip("/")
    if not normalized:
        return "unknown", "unknown"

    grpc_service, separator, grpc_method = normalized.rpartition("/")
    if not separator:
        return metric_label(normalized), "unknown"

    return metric_label(grpc_service), metric_label(grpc_method)


def _status_label(status_code: grpc.StatusCode | None) -> str:
    if status_code is None:
        return "unknown"
    return metric_label(getattr(status_code, "name", status_code))


def _context_code(context: Any) -> grpc.StatusCode | None:
    code_resolver = getattr(context, "code", None)
    if not callable(code_resolver):
        return None

    code = code_resolver()
    if isinstance(code, grpc.StatusCode):
        return code
    return None


class GrpcServerMetricsInterceptor(grpc.aio.ServerInterceptor):
    """Record Prometheus metrics for unary-unary gRPC server calls."""

    def __init__(self, service_name: str) -> None:
        self._service_name = metric_label(service_name)

    async def intercept_service(
        self,
        continuation: Any,
        handler_call_details: grpc.HandlerCallDetails,
    ) -> Any:
        method = str(handler_call_details.method or "")
        if any(marker in method for marker in _SYSTEM_METHOD_MARKERS):
            return await continuation(handler_call_details)

        handler = await continuation(handler_call_details)
        if handler is None or handler.unary_unary is None:
            return handler

        grpc_service, grpc_method = _split_method(method)

        async def _wrapped_unary_unary(
            request: Any,
            context: grpc.aio.ServicerContext,
        ) -> Any:
            started_at = time.perf_counter()
            status_code = grpc.StatusCode.OK
            try:
                response = await handler.unary_unary(request, context)
            except grpc.RpcError as exc:
                status_code = exc.code()
                raise
            except Exception:
                status_code = _context_code(context) or grpc.StatusCode.UNKNOWN
                raise
            else:
                status_code = _context_code(context) or grpc.StatusCode.OK
                return response
            finally:
                elapsed = time.perf_counter() - started_at
                _SERVER_REQUESTS_TOTAL.labels(
                    service=self._service_name,
                    grpc_service=grpc_service,
                    grpc_method=grpc_method,
                    grpc_code=_status_label(status_code),
                ).inc()
                _SERVER_REQUEST_DURATION_SECONDS.labels(
                    service=self._service_name,
                    grpc_service=grpc_service,
                    grpc_method=grpc_method,
                ).observe(elapsed)

        return grpc.unary_unary_rpc_method_handler(
            _wrapped_unary_unary,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )


class GrpcClientMetricsInterceptor(grpc.aio.UnaryUnaryClientInterceptor):
    """Record Prometheus metrics for unary-unary gRPC client calls."""

    def __init__(self, *, service_name: str, target: str) -> None:
        self._service_name = metric_label(service_name)
        self._target = metric_label(target)

    async def intercept_unary_unary(
        self,
        continuation: Any,
        client_call_details: grpc.aio.ClientCallDetails,
        request: Any,
    ) -> Any:
        grpc_service, grpc_method = _split_method(client_call_details.method)
        started_at = time.perf_counter()
        status_code = grpc.StatusCode.OK
        try:
            response = await continuation(client_call_details, request)
        except grpc.RpcError as exc:
            status_code = exc.code()
            raise
        except Exception:
            status_code = grpc.StatusCode.UNKNOWN
            raise
        else:
            return response
        finally:
            elapsed = time.perf_counter() - started_at
            _CLIENT_REQUESTS_TOTAL.labels(
                service=self._service_name,
                target=self._target,
                grpc_service=grpc_service,
                grpc_method=grpc_method,
                grpc_code=_status_label(status_code),
            ).inc()
            _CLIENT_REQUEST_DURATION_SECONDS.labels(
                service=self._service_name,
                target=self._target,
                grpc_service=grpc_service,
                grpc_method=grpc_method,
            ).observe(elapsed)


__all__ = [
    "GrpcClientMetricsInterceptor",
    "GrpcServerMetricsInterceptor",
]
