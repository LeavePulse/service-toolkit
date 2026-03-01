from __future__ import annotations

from litestar import Litestar
from litestar.middleware.base import DefineMiddleware
from litestar.testing import TestClient
from prometheus_client import Gauge

from service_toolkit.prometheus import build_prometheus_instrumentation


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
