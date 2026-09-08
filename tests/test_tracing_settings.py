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
