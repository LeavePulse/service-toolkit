"""Prometheus instrumentation helpers."""

from __future__ import annotations

import os
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from litestar import Response, get
from litestar.types import ASGIApp, ControllerRouterHandler
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    REGISTRY,
    generate_latest,
)
from prometheus_client import multiprocess

Scope = dict[str, object]
Receive = Callable[[], Awaitable[dict[str, object]]]
Send = Callable[[dict[str, object]], Awaitable[None]]
MiddlewareFactory = Callable[..., ASGIApp]


def _normalize_service_name(service_name: str) -> str:
    return service_name.strip().lower().replace("-", "_").replace(" ", "_")


def build_prometheus_instrumentation(
    *,
    service_name: str,
    route: str = "/metrics",
    registry: CollectorRegistry | None = None,
) -> tuple[MiddlewareFactory, ControllerRouterHandler]:
    """Construct Prometheus middleware and metrics endpoint for a service.

    Args:
        service_name: Logical identifier for the service. Used to namespace metrics.
        route: HTTP path that serves the Prometheus metrics endpoint.
        registry: Optional custom collector registry. A new registry is created by default.

    Returns:
        Tuple containing the middleware class and the Litestar route handler.
    """

    normalized = _normalize_service_name(service_name)
    multiprocess_dir = os.getenv("PROMETHEUS_MULTIPROC_DIR") or os.getenv(
        "prometheus_multiproc_dir"
    )
    multiprocess_enabled = False

    if registry is not None:
        metrics_registry = registry
    else:
        metrics_registry = CollectorRegistry()
        if multiprocess_dir:
            multiprocess_enabled = True
            target_dir = Path(multiprocess_dir)
            target_dir.mkdir(parents=True, exist_ok=True)
            multiprocess.MultiProcessCollector(metrics_registry)

    # In multiprocess mode, metrics must be registered in the default registry.
    # The endpoint registry is dedicated to the multiprocess collector.
    instrumentation_registry = REGISTRY if multiprocess_enabled else metrics_registry

    request_count = Counter(
        f"{normalized}_http_requests_total",
        "Total HTTP requests processed by the service",
        ["method", "route", "status"],
        registry=instrumentation_registry,
    )
    request_latency = Histogram(
        f"{normalized}_http_request_duration_seconds",
        "Latency of HTTP requests processed by the service",
        ["method", "route"],
        registry=instrumentation_registry,
    )

    def resolve_route_label(scope: Scope) -> str:
        route_handler = scope.get("route_handler")
        if route_handler is not None:
            pattern = getattr(route_handler, "path", None)
            if pattern:
                return str(pattern)
        return str(scope.get("path", "unknown"))

    class _PrometheusMiddleware:
        """ASGI middleware that records Prometheus metrics for HTTP traffic."""

        def __init__(self, app: ASGIApp) -> None:
            self.app = app

        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
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

            async def send_wrapper(message: dict[str, object]) -> None:
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

    def _prometheus_middleware(app: ASGIApp) -> ASGIApp:
        return _PrometheusMiddleware(app)

    def metrics_handler() -> Response[bytes]:
        payload = generate_latest(metrics_registry)
        return Response(payload, media_type=CONTENT_TYPE_LATEST)

    metrics_endpoint = get(route, include_in_schema=False, sync_to_thread=False)(
        metrics_handler
    )

    return _prometheus_middleware, metrics_endpoint


def prepare_multiprocess_directory(directory: str | os.PathLike[str] | None = None) -> Path | None:
    """Create (and clean) the prometheus multiprocess directory.

    Call this once in the parent process before spawning worker processes.
    """

    resolved_directory: str | os.PathLike[str] | None = directory
    if resolved_directory is None:
        resolved_directory = os.getenv("PROMETHEUS_MULTIPROC_DIR") or os.getenv(
            "prometheus_multiproc_dir"
        )
    if not resolved_directory:
        return None
    target = Path(resolved_directory)

    target.mkdir(parents=True, exist_ok=True)
    for item in target.iterdir():
        if item.is_file():
            item.unlink(missing_ok=True)
    return target


__all__ = ["build_prometheus_instrumentation", "prepare_multiprocess_directory"]
