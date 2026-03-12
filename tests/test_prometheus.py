from __future__ import annotations

from litestar import Litestar, get
from litestar.exceptions import NotAuthorizedException
from litestar.middleware.base import DefineMiddleware
from litestar.testing import TestClient
from prometheus_client import CollectorRegistry, Gauge

from service_toolkit.observability.prometheus import build_prometheus_instrumentation


def test_prometheus_instrumentation_exposes_metrics() -> None:
    PrometheusMiddleware, metrics_endpoint = build_prometheus_instrumentation(
        service_name="test_service",
        route="/metrics",
    )

    app = Litestar(
        route_handlers=[metrics_endpoint],
        middleware=[DefineMiddleware(PrometheusMiddleware)],
    )

    with TestClient(app) as client:
        response = client.get("/metrics")

    assert response.status_code == 200
    assert "test_service_http_requests_total" in response.text


def test_prometheus_instrumentation_exposes_custom_global_metrics() -> None:
    metric_name = "test_service_custom_metric_visibility"
    custom_metric = Gauge(
        metric_name,
        "Custom metric should be present on /metrics endpoint",
    )
    custom_metric.set(1)

    PrometheusMiddleware, metrics_endpoint = build_prometheus_instrumentation(
        service_name="test_service_custom",
        route="/metrics",
    )

    app = Litestar(
        route_handlers=[metrics_endpoint],
        middleware=[DefineMiddleware(PrometheusMiddleware)],
    )

    with TestClient(app) as client:
        response = client.get("/metrics")

    assert response.status_code == 200
    assert metric_name in response.text


def test_prometheus_instrumentation_uses_http_exception_status() -> None:
    registry = CollectorRegistry()
    PrometheusMiddleware, metrics_endpoint = build_prometheus_instrumentation(
        service_name="test_service_http_exception",
        route="/metrics",
        registry=registry,
    )

    class RejectingAuthMiddleware:
        def __init__(self, app) -> None:
            self.app = app

        async def __call__(self, scope, receive, send) -> None:
            if scope.get("type") == "http" and scope.get("path") == "/protected":
                raise NotAuthorizedException("Missing or invalid Bearer token.")
            await self.app(scope, receive, send)

    @get("/protected")
    def protected() -> dict[str, str]:
        return {"ok": "true"}

    app = Litestar(
        route_handlers=[protected, metrics_endpoint],
        middleware=[
            DefineMiddleware(PrometheusMiddleware),
            DefineMiddleware(RejectingAuthMiddleware),
        ],
    )

    with TestClient(app) as client:
        response = client.get("/protected")
        metrics = client.get("/metrics")

    assert response.status_code == 401
    assert (
        'test_service_http_exception_http_requests_total{method="GET",route="/protected",status="401"} 1.0'
        in metrics.text
    )
