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
