import pytest

from service_toolkit.observability.tracing import setup_tracing
from service_toolkit.settings import TracingSettings


def test_tracing_settings_load_standard_otel_names() -> None:
    settings = TracingSettings.load(
        env={
            "OTEL_ENABLED": "true",
            "OTEL_EXPORTER_OTLP_ENDPOINT": "https://tempo.internal:4317",
            "OTEL_EXPORTER_OTLP_HEADERS": "x-tenant=ops",
            "OTEL_EXPORTER_OTLP_INSECURE": "false",
            "OTEL_TRACES_SAMPLER_ARG": "0.25",
            "OTEL_RESOURCE_ATTRIBUTES": "deployment.environment.name=prod",
            "OTEL_INSTRUMENT_HTTPX": "false",
        },
        env_file=None,
        prefix="OTEL_",
    )

    assert settings.enabled is True
    assert settings.exporter_otlp_endpoint == "https://tempo.internal:4317"
    assert settings.exporter_otlp_headers == "x-tenant=ops"
    assert settings.exporter_otlp_insecure is False
    assert settings.traces_sampler_arg == 0.25
    assert settings.resource_attributes == "deployment.environment.name=prod"
    assert settings.instrument_httpx is False


def test_disabled_tracing_never_imports_optional_instrumentation() -> None:
    assert setup_tracing(
        service_name="test-service",
        settings=TracingSettings(enabled=False),
    ) is None


def test_app_factory_passes_typed_tracing_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from service_toolkit.observability import tracing
    from service_toolkit.web.app_factory import create_service_app

    captured: list[TracingSettings | None] = []

    def capture_settings(
        *,
        service_name: str,
        settings: TracingSettings | None = None,
        instrument_sqlalchemy: bool | None = None,
        instrument_redis: bool | None = None,
    ) -> None:
        _ = (service_name, instrument_sqlalchemy, instrument_redis)
        captured.append(settings)

    monkeypatch.setattr(tracing, "setup_tracing", capture_settings)
    policy = TracingSettings(enabled=False)

    create_service_app(
        service_name="tracing-policy-test",
        openapi_title="Tracing policy test",
        route_handlers=[],
        tracing_settings=policy,
    )

    assert captured == [policy]


def test_failed_unsampled_spans_are_exported_and_others_dropped() -> None:
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )
    from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
    from opentelemetry.trace import Status, StatusCode

    from service_toolkit.observability.tracing import (
        _failure_exporting_processor,
        _record_unsampled_class,
    )

    exporter = InMemorySpanExporter()
    provider = TracerProvider(
        sampler=ParentBased(_record_unsampled_class()(TraceIdRatioBased(0.0)))
    )
    provider.add_span_processor(_failure_exporting_processor()(exporter))
    tracer = provider.get_tracer("test")

    with tracer.start_as_current_span("quiet") as quiet:
        assert quiet.is_recording()
    with tracer.start_as_current_span("broken") as broken:
        broken.set_status(Status(StatusCode.ERROR, "boom"))

    provider.force_flush()
    assert [span.name for span in exporter.get_finished_spans()] == ["broken"]
    provider.shutdown()


def test_sampled_spans_are_exported_by_failure_processor() -> None:
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )
    from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

    from service_toolkit.observability.tracing import (
        _failure_exporting_processor,
        _record_unsampled_class,
    )

    exporter = InMemorySpanExporter()
    provider = TracerProvider(
        sampler=ParentBased(_record_unsampled_class()(TraceIdRatioBased(1.0)))
    )
    provider.add_span_processor(_failure_exporting_processor()(exporter))

    with provider.get_tracer("test").start_as_current_span("fine"):
        pass

    provider.force_flush()
    assert [span.name for span in exporter.get_finished_spans()] == ["fine"]
    provider.shutdown()


def test_disabled_instrumentations_are_not_imported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    from opentelemetry import trace

    from service_toolkit.observability import tracing

    for module in (
        "opentelemetry.instrumentation.sqlalchemy",
        "opentelemetry.instrumentation.asgi",
        "opentelemetry.instrumentation.httpx",
        "opentelemetry.instrumentation.redis",
    ):
        monkeypatch.setitem(sys.modules, module, None)
    monkeypatch.setattr(tracing, "_configured", False)
    installed: list[object] = []
    monkeypatch.setattr(trace, "set_tracer_provider", installed.append)

    middleware = setup_tracing(
        service_name="worker",
        settings=TracingSettings(enabled=True),
        instrument_httpx=False,
        instrument_sqlalchemy=False,
        instrument_redis=False,
    )

    assert middleware is not None
    assert len(installed) == 1
    with pytest.raises(ModuleNotFoundError, match="tracing"):
        middleware(object())  # type: ignore[arg-type]
    installed[0].shutdown()  # type: ignore[attr-defined]
