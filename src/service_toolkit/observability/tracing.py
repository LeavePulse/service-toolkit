"""OpenTelemetry tracing bootstrap helpers."""

from __future__ import annotations

import importlib
import logging
import threading
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast

from service_toolkit.settings import TracingSettings

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
    settings: TracingSettings | None = None,
    enabled: bool | None = None,
    instrument_httpx: bool | None = None,
    instrument_sqlalchemy: bool | None = None,
    instrument_redis: bool | None = None,
    export_failed_unsampled: bool = False,
) -> MiddlewareFactory | None:
    """Configure global OpenTelemetry provider and return ASGI middleware class.

    ``settings`` is the typed observability policy. Omitting it loads the
    standard ``OTEL_*`` spelling through :class:`TracingSettings`, preserving
    compatibility while keeping environment parsing out of tracing logic.

    ``export_failed_unsampled`` keeps errors visible under partial sampling:
    traces the ratio sampler would drop are still recorded in memory, and a
    span that ends with an error status is exported anyway. Without it a
    sampling ratio below 1 silently loses that share of failures, so a trace
    link attached to an error report leads nowhere.
    """

    tracing = settings or TracingSettings.load(prefix="OTEL_")
    enabled_value = tracing.enabled if enabled is None else bool(enabled)
    if not enabled_value:
        return None

    endpoint = tracing.exporter_otlp_endpoint.strip() or "http://tempo:4317"
    headers = _parse_otel_headers(tracing.exporter_otlp_headers)
    sample_ratio = max(0.0, min(1.0, tracing.traces_sampler_arg))

    if tracing.exporter_otlp_insecure is None:
        insecure = endpoint.startswith("http://")
    else:
        insecure = tracing.exporter_otlp_insecure

    httpx_enabled = (
        tracing.instrument_httpx
        if instrument_httpx is None
        else bool(instrument_httpx)
    )
    sqlalchemy_enabled = (
        tracing.instrument_sqlalchemy
        if instrument_sqlalchemy is None
        else bool(instrument_sqlalchemy)
    )
    redis_enabled = (
        tracing.instrument_redis
        if instrument_redis is None
        else bool(instrument_redis)
    )

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
    except ModuleNotFoundError as exc:  # pragma: no cover - optional dependency
        raise _missing_extra(exc) from exc

    global _configured, _configured_service_name
    global _httpx_instrumented, _sqlalchemy_instrumented, _redis_instrumented

    with _configure_lock:
        if not _configured:
            resource_attributes = _parse_resource_attributes(tracing.resource_attributes)
            resource_attributes.setdefault(SERVICE_NAME, service_name)
            resource = Resource.create(resource_attributes)
            ratio_sampler = TraceIdRatioBased(sample_ratio)
            if export_failed_unsampled:
                root_sampler = _record_unsampled_class()(ratio_sampler)
                processor_class = _failure_exporting_processor()
            else:
                root_sampler = ratio_sampler
                processor_class = BatchSpanProcessor
            provider = TracerProvider(
                resource=resource,
                sampler=ParentBased(root_sampler),
            )
            exporter = OTLPSpanExporter(
                endpoint=endpoint,
                headers=dict(headers),
                insecure=bool(insecure),
            )
            provider.add_span_processor(processor_class(exporter))
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
            _optional(
                "opentelemetry.instrumentation.httpx", "HTTPXClientInstrumentor"
            )().instrument()
            _httpx_instrumented = True

        if sqlalchemy_enabled and not _sqlalchemy_instrumented:
            _optional(
                "opentelemetry.instrumentation.sqlalchemy", "SQLAlchemyInstrumentor"
            )().instrument(enable_commenter=False)
            _sqlalchemy_instrumented = True

        if redis_enabled and not _redis_instrumented:
            _optional(
                "opentelemetry.instrumentation.redis", "RedisInstrumentor"
            )().instrument()
            _redis_instrumented = True

    def _otel_middleware(app: ASGIApp) -> ASGIApp:
        middleware = _optional(
            "opentelemetry.instrumentation.asgi", "OpenTelemetryMiddleware"
        )
        return cast("ASGIApp", middleware(app))

    return _otel_middleware


def _missing_extra(exc: ModuleNotFoundError) -> ModuleNotFoundError:
    missing = str(exc.name or "opentelemetry")
    return ModuleNotFoundError(
        "Tracing support requires the optional 'tracing' extra. "
        "Install with 'pip install service-toolkit[tracing]'. "
        f"Missing module: {missing}"
    )


def _optional(module: str, name: str) -> Any:
    """Import one instrumentation only when it is switched on.

    A worker without SQLAlchemy or an ASGI server can still trace: the
    sqlalchemy instrumentation imports sqlalchemy itself, and the ASGI one
    asgiref, so importing all of them up front made every consumer install
    every instrumented library.
    """
    try:
        return getattr(importlib.import_module(module), name)
    except ModuleNotFoundError as exc:
        raise _missing_extra(exc) from exc


def _record_unsampled_class() -> type[Any]:
    from opentelemetry.sdk.trace.sampling import Decision, Sampler, SamplingResult

    class RecordUnsampled(Sampler):
        """Ratio sampling that records, instead of dropping, the rest."""

        def __init__(self, inner: Sampler) -> None:
            self._inner = inner

        def should_sample(self, *args: Any, **kwargs: Any) -> SamplingResult:
            result = self._inner.should_sample(*args, **kwargs)
            if result.decision is Decision.DROP:
                return SamplingResult(
                    Decision.RECORD_ONLY, None, result.trace_state
                )
            return result

        def get_description(self) -> str:
            return f"RecordUnsampled{{{self._inner.get_description()}}}"

    return RecordUnsampled


def _failure_exporting_processor() -> type[Any]:
    from opentelemetry.sdk.trace import ReadableSpan
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.trace import StatusCode

    class FailureExportingProcessor(BatchSpanProcessor):
        """Batch export of sampled spans plus unsampled ones that failed."""

        def on_end(self, span: ReadableSpan) -> None:
            context = span.context
            sampled = bool(context and context.trace_flags.sampled)
            if sampled or span.status.status_code is StatusCode.ERROR:
                # Not ``super().on_end``: its first check discards every
                # unsampled span, failed or not.
                self._batch_processor.emit(span)

    return FailureExportingProcessor


__all__ = ["setup_tracing"]
