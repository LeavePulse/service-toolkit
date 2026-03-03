"""OpenTelemetry tracing bootstrap helpers."""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Mapping
from typing import TYPE_CHECKING, cast

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Callable

    from litestar.types import ASGIApp

    MiddlewareFactory = Callable[..., ASGIApp]

_configure_lock = threading.Lock()
_configured = False
_configured_service_name: str | None = None
_httpx_instrumented = False
_sqlalchemy_instrumented = False
_redis_instrumented = False


def _env_bool(name: str, *, default: bool = False) -> bool:
    raw = str(os.getenv(name, "")).strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_float(name: str, *, default: float) -> float:
    raw = str(os.getenv(name, "")).strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _parse_otel_headers(raw: str | None) -> Mapping[str, str]:
    if not raw:
        return {}
    headers: dict[str, str] = {}
    for pair in raw.split(","):
        key, _, value = pair.partition("=")
        key = key.strip()
        value = value.strip()
        if key and value:
            headers[key] = value
    return headers


def _parse_resource_attributes(raw: str | None) -> dict[str, str]:
    attributes: dict[str, str] = {}
    if not raw:
        return attributes
    for pair in raw.split(","):
        key, _, value = pair.partition("=")
        key = key.strip()
        value = value.strip()
        if key and value:
            attributes[key] = value
    return attributes


def setup_tracing(
    *,
    service_name: str,
    enabled: bool | None = None,
    instrument_httpx: bool | None = None,
    instrument_sqlalchemy: bool | None = None,
    instrument_redis: bool | None = None,
) -> MiddlewareFactory | None:
    """Configure global OpenTelemetry provider and return ASGI middleware class.

    Environment:
    - `OTEL_ENABLED` (bool, default: `false`)
    - `OTEL_EXPORTER_OTLP_ENDPOINT` (default: `http://tempo:4317`)
    - `OTEL_EXPORTER_OTLP_HEADERS` (comma-separated `k=v`)
    - `OTEL_EXPORTER_OTLP_INSECURE` (bool, default: inferred from endpoint scheme)
    - `OTEL_TRACES_SAMPLER_ARG` (float, default: `1.0`)
    - `OTEL_RESOURCE_ATTRIBUTES` (comma-separated `k=v`)
    - `OTEL_INSTRUMENT_HTTPX` (bool, default: `true`)
    - `OTEL_INSTRUMENT_SQLALCHEMY` (bool, default: `true`)
    - `OTEL_INSTRUMENT_REDIS` (bool, default: `true`)
    """

    enabled_value = (
        _env_bool("OTEL_ENABLED", default=False) if enabled is None else bool(enabled)
    )
    if not enabled_value:
        return None

    endpoint = (
        str(os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")).strip() or "http://tempo:4317"
    )
    headers = _parse_otel_headers(os.getenv("OTEL_EXPORTER_OTLP_HEADERS"))
    sample_ratio = max(
        0.0, min(1.0, _env_float("OTEL_TRACES_SAMPLER_ARG", default=1.0))
    )

    insecure_env = os.getenv("OTEL_EXPORTER_OTLP_INSECURE")
    if insecure_env is None:
        insecure = endpoint.startswith("http://")
    else:
        insecure = _env_bool("OTEL_EXPORTER_OTLP_INSECURE", default=False)

    httpx_enabled = (
        _env_bool("OTEL_INSTRUMENT_HTTPX", default=True)
        if instrument_httpx is None
        else bool(instrument_httpx)
    )
    sqlalchemy_enabled = (
        _env_bool("OTEL_INSTRUMENT_SQLALCHEMY", default=True)
        if instrument_sqlalchemy is None
        else bool(instrument_sqlalchemy)
    )
    redis_enabled = (
        _env_bool("OTEL_INSTRUMENT_REDIS", default=True)
        if instrument_redis is None
        else bool(instrument_redis)
    )

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.instrumentation.redis import RedisInstrumentor
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
    except ModuleNotFoundError as exc:  # pragma: no cover - optional dependency
        missing = str(exc.name or "opentelemetry")
        raise ModuleNotFoundError(
            "Tracing support requires the optional 'tracing' extra. "
            "Install with 'pip install service-toolkit[tracing]'. "
            f"Missing module: {missing}"
        ) from exc

    global _configured, _configured_service_name
    global _httpx_instrumented, _sqlalchemy_instrumented, _redis_instrumented

    with _configure_lock:
        if not _configured:
            resource_attributes = _parse_resource_attributes(
                os.getenv("OTEL_RESOURCE_ATTRIBUTES")
            )
            resource_attributes.setdefault(SERVICE_NAME, service_name)
            resource = Resource.create(resource_attributes)
            provider = TracerProvider(
                resource=resource,
                sampler=ParentBased(TraceIdRatioBased(sample_ratio)),
            )
            exporter = OTLPSpanExporter(
                endpoint=endpoint,
                headers=dict(headers),
                insecure=bool(insecure),
            )
            provider.add_span_processor(BatchSpanProcessor(exporter))
            trace.set_tracer_provider(provider)
            _configured = True
            _configured_service_name = service_name
        elif _configured_service_name and _configured_service_name != service_name:
            logger.warning(
                "OpenTelemetry provider already configured for %s; "
                "requested reconfigure for %s is ignored",
                _configured_service_name,
                service_name,
            )

        if httpx_enabled and not _httpx_instrumented:
            HTTPXClientInstrumentor().instrument()
            _httpx_instrumented = True

        if sqlalchemy_enabled and not _sqlalchemy_instrumented:
            SQLAlchemyInstrumentor().instrument(enable_commenter=False)
            _sqlalchemy_instrumented = True

        if redis_enabled and not _redis_instrumented:
            RedisInstrumentor().instrument()
            _redis_instrumented = True

    def _otel_middleware(app: ASGIApp) -> ASGIApp:
        return cast("ASGIApp", OpenTelemetryMiddleware(app))

    return _otel_middleware


__all__ = ["setup_tracing"]
