"""Prometheus instrumentation helpers."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Tuple

from litestar import Response, get
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)

ASGIApp = Callable[..., Any]


def _normalize_service_name(service_name: str) -> str:
    return service_name.strip().lower().replace("-", "_").replace(" ", "_")


def build_prometheus_instrumentation(
    *,
    service_name: str,
    route: str = "/metrics",
    registry: CollectorRegistry | None = None,
) -> Tuple[type, Callable[..., Response[bytes]]]:
    """Construct Prometheus middleware and metrics endpoint for a service.

    Args:
        service_name: Logical identifier for the service. Used to namespace metrics.
        route: HTTP path that serves the Prometheus metrics endpoint.
        registry: Optional custom collector registry. A new registry is created by default.

    Returns:
        Tuple containing the middleware class and the Litestar route handler.
    """

    normalized = _normalize_service_name(service_name)
    metrics_registry = registry or CollectorRegistry()

    request_count = Counter(
        f"{normalized}_http_requests_total",
        "Total HTTP requests processed by the service",
        ["method", "route", "status"],
        registry=metrics_registry,
    )
    request_latency = Histogram(
        f"{normalized}_http_request_duration_seconds",
        "Latency of HTTP requests processed by the service",
        ["method", "route"],
        registry=metrics_registry,
    )

    def resolve_route_label(scope: dict[str, Any]) -> str:
        route_handler = scope.get("route_handler")
        if route_handler is not None:
            pattern = getattr(route_handler, "path", None)
            if pattern:
                return str(pattern)
        return str(scope.get("path", "unknown"))

    class PrometheusMiddleware:
        """ASGI middleware that records Prometheus metrics for HTTP traffic."""

        def __init__(self, app: ASGIApp) -> None:
            self.app = app

        async def __call__(
            self, scope: dict[str, Any], receive: Callable, send: Callable
        ) -> None:  # type: ignore[override]
            if scope.get("type") != "http":
                await self.app(scope, receive, send)
                return

            path = resolve_route_label(scope)
            if path == route:
                await self.app(scope, receive, send)
                return

            method = str(scope.get("method", "UNKNOWN"))
            status_code = 500
            start = time.perf_counter()

            async def send_wrapper(message: dict[str, Any]) -> None:
                nonlocal status_code
                if message.get("type") == "http.response.start":
                    status_code = int(message.get("status", 500))
                await send(message)

            try:
                await self.app(scope, receive, send_wrapper)
            finally:
                elapsed = time.perf_counter() - start
                request_count.labels(
                    method=method, route=path, status=str(status_code)
                ).inc()
                request_latency.labels(method=method, route=path).observe(elapsed)

    def metrics_handler() -> Response[bytes]:
        payload = generate_latest(metrics_registry)
        return Response(payload, media_type=CONTENT_TYPE_LATEST)

    metrics_endpoint = get(route, include_in_schema=False, sync_to_thread=False)(
        metrics_handler
    )

    return PrometheusMiddleware, metrics_endpoint


__all__ = ["build_prometheus_instrumentation"]
