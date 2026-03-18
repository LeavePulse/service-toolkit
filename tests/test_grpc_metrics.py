from __future__ import annotations

from types import SimpleNamespace

import grpc
import pytest
from prometheus_client import REGISTRY

from service_toolkit.grpc.metrics import (
    GrpcClientMetricsInterceptor,
    GrpcServerMetricsInterceptor,
)


class _FakeRpcError(grpc.RpcError):
    def __init__(self, code: grpc.StatusCode) -> None:
        super().__init__()
        self._code = code

    def code(self) -> grpc.StatusCode:
        return self._code


class _FakeContext:
    def __init__(self, code: grpc.StatusCode | None = None) -> None:
        self._code = code

    def code(self) -> grpc.StatusCode | None:
        return self._code


def _sample_value(name: str, labels: dict[str, str]) -> float:
    value = REGISTRY.get_sample_value(name, labels)
    return 0.0 if value is None else float(value)


@pytest.mark.asyncio
async def test_server_metrics_interceptor_records_success() -> None:
    service_name = "grpc_metrics_server_success"
    grpc_service = "leavepulse.test.v1.exampleservice"
    grpc_method = "getone"
    counter_labels = {
        "service": service_name,
        "grpc_service": grpc_service,
        "grpc_method": grpc_method,
        "grpc_code": "ok",
    }
    histogram_labels = {
        "service": service_name,
        "grpc_service": grpc_service,
        "grpc_method": grpc_method,
    }
    before_total = _sample_value(
        "leavepulse_grpc_server_requests_total", counter_labels
    )
    before_count = _sample_value(
        "leavepulse_grpc_server_request_duration_seconds_count",
        histogram_labels,
    )

    interceptor = GrpcServerMetricsInterceptor(service_name)

    async def _handler(request: object, context: _FakeContext) -> str:
        return "ok"

    handler = grpc.unary_unary_rpc_method_handler(_handler)

    async def _continuation(_: object) -> grpc.RpcMethodHandler:
        return handler

    wrapped = await interceptor.intercept_service(
        _continuation,
        SimpleNamespace(
            method="/leavepulse.test.v1.ExampleService/GetOne",
            invocation_metadata=(),
        ),
    )

    assert wrapped is not None
    assert wrapped.unary_unary is not None
    response = await wrapped.unary_unary(object(), _FakeContext())

    assert response == "ok"
    assert _sample_value("leavepulse_grpc_server_requests_total", counter_labels) == (
        before_total + 1
    )
    assert _sample_value(
        "leavepulse_grpc_server_request_duration_seconds_count",
        histogram_labels,
    ) == (before_count + 1)


@pytest.mark.asyncio
async def test_client_metrics_interceptor_records_failure() -> None:
    service_name = "grpc_metrics_client_failure"
    target = "monitoring-service:50200"
    grpc_service = "leavepulse.monitoring.v1.monitoringliveservice"
    grpc_method = "getlive"
    counter_labels = {
        "service": service_name,
        "target": target,
        "grpc_service": grpc_service,
        "grpc_method": grpc_method,
        "grpc_code": "not_found",
    }
    histogram_labels = {
        "service": service_name,
        "target": target,
        "grpc_service": grpc_service,
        "grpc_method": grpc_method,
    }
    before_total = _sample_value(
        "leavepulse_grpc_client_requests_total", counter_labels
    )
    before_count = _sample_value(
        "leavepulse_grpc_client_request_duration_seconds_count",
        histogram_labels,
    )

    interceptor = GrpcClientMetricsInterceptor(
        service_name=service_name,
        target=target,
    )
    client_call_details = SimpleNamespace(
        method="/leavepulse.monitoring.v1.MonitoringLiveService/GetLive",
        timeout=None,
        metadata=None,
        credentials=None,
        wait_for_ready=None,
    )

    async def _continuation(_: object, __: object) -> object:
        raise _FakeRpcError(grpc.StatusCode.NOT_FOUND)

    with pytest.raises(_FakeRpcError):
        await interceptor.intercept_unary_unary(
            _continuation,
            client_call_details,
            object(),
        )

    assert _sample_value("leavepulse_grpc_client_requests_total", counter_labels) == (
        before_total + 1
    )
    assert _sample_value(
        "leavepulse_grpc_client_request_duration_seconds_count",
        histogram_labels,
    ) == (before_count + 1)
