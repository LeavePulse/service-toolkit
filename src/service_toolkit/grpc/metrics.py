"""Prometheus instrumentation for gRPC traffic."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from typing import Any

import grpc
from prometheus_client import Counter, Histogram

from ..observability.metrics import metric_label

logger = logging.getLogger(__name__)

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


def _normalize_method_name(method: str | bytes | None) -> str:
    if isinstance(method, bytes):
        return method.decode("utf-8", errors="ignore")
    return str(method or "")


def _split_method(method: str | bytes | None) -> tuple[str, str]:
    normalized = _normalize_method_name(method).strip().lstrip("/")
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


def _coerce_status_code(status_code: object | None) -> grpc.StatusCode | None:
    if isinstance(status_code, grpc.StatusCode):
        return status_code
    return None


def _context_code(context: Any) -> grpc.StatusCode | None:
    code_resolver = getattr(context, "code", None)
    if not callable(code_resolver):
        return None

    return _coerce_status_code(code_resolver())


def _record_client_metrics(
    *,
    service_name: str,
    target: str,
    grpc_service: str,
    grpc_method: str,
    status_code: grpc.StatusCode | None,
    started_at: float,
) -> None:
    elapsed = time.perf_counter() - started_at
    _CLIENT_REQUESTS_TOTAL.labels(
        service=service_name,
        target=target,
        grpc_service=grpc_service,
        grpc_method=grpc_method,
        grpc_code=_status_label(status_code),
    ).inc()
    _CLIENT_REQUEST_DURATION_SECONDS.labels(
        service=service_name,
        target=target,
        grpc_service=grpc_service,
        grpc_method=grpc_method,
    ).observe(elapsed)


async def _call_status_code(call: Any) -> grpc.StatusCode | None:
    code_resolver = getattr(call, "code", None)
    if not callable(code_resolver):
        return None

    code = code_resolver()
    if inspect.isawaitable(code):
        code = await code
    return _coerce_status_code(code)


async def _observe_completed_client_call(
    call: Any,
    *,
    service_name: str,
    target: str,
    grpc_service: str,
    grpc_method: str,
    started_at: float,
) -> None:
    status_code: grpc.StatusCode | None = None
    try:
        status_code = await _call_status_code(call)
    except grpc.RpcError as exc:
        status_code = exc.code()
    except Exception:
        logger.debug("failed to read gRPC status code", exc_info=True)
        status_code = None

    if status_code is None and bool(getattr(call, "cancelled", lambda: False)()):
        status_code = grpc.StatusCode.CANCELLED
    if status_code is None:
        status_code = grpc.StatusCode.UNKNOWN

    _record_client_metrics(
        service_name=service_name,
        target=target,
        grpc_service=grpc_service,
        grpc_method=grpc_method,
        status_code=status_code,
        started_at=started_at,
    )


class GrpcServerMetricsInterceptor(grpc.aio.ServerInterceptor):
    """Record Prometheus metrics for unary-unary gRPC server calls."""

    def __init__(self, service_name: str) -> None:
        self._service_name = metric_label(service_name)

    async def intercept_service(
        self,
        continuation: Any,
        handler_call_details: grpc.HandlerCallDetails,
    ) -> Any:
        method = _normalize_method_name(handler_call_details.method)
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
        try:
            call = await continuation(client_call_details, request)
        except grpc.RpcError as exc:
            _record_client_metrics(
                service_name=self._service_name,
                target=self._target,
                grpc_service=grpc_service,
                grpc_method=grpc_method,
                status_code=exc.code(),
                started_at=started_at,
            )
            raise
        except Exception as exc:
            logger.debug("gRPC call failed; recording error metrics", exc_info=exc)
            _record_client_metrics(
                service_name=self._service_name,
                target=self._target,
                grpc_service=grpc_service,
                grpc_method=grpc_method,
                status_code=grpc.StatusCode.UNKNOWN,
                started_at=started_at,
            )
            raise

        add_done_callback = getattr(call, "add_done_callback", None)
        if not callable(add_done_callback):
            _record_client_metrics(
                service_name=self._service_name,
                target=self._target,
                grpc_service=grpc_service,
                grpc_method=grpc_method,
                status_code=grpc.StatusCode.OK,
                started_at=started_at,
            )
            return call

        def _on_done(completed_call: Any) -> None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                _record_client_metrics(
                    service_name=self._service_name,
                    target=self._target,
                    grpc_service=grpc_service,
                    grpc_method=grpc_method,
                    status_code=grpc.StatusCode.UNKNOWN,
                    started_at=started_at,
                )
                return
            loop.create_task(
                _observe_completed_client_call(
                    completed_call,
                    service_name=self._service_name,
                    target=self._target,
                    grpc_service=grpc_service,
                    grpc_method=grpc_method,
                    started_at=started_at,
                )
            )

        add_done_callback(_on_done)
        return call


__all__ = [
    "GrpcClientMetricsInterceptor",
    "GrpcServerMetricsInterceptor",
]
