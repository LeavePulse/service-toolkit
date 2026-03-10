"""Observability helpers: logging, metrics, tracing."""

from __future__ import annotations

import importlib
import sys

__all__ = [
    "RequestContextLoggingMiddleware",
    "ThrottledGaugeRefresh",
    "bind_log_user_id",
    "build_prometheus_instrumentation",
    "build_standard_logging_config",
    "get_log_context",
    "metric_label",
    "prepare_multiprocess_directory",
    "request_context_middleware",
    "setup_tracing",
]

_EXPORT_MODULES = {
    "RequestContextLoggingMiddleware": ".logging",
    "ThrottledGaugeRefresh": ".metrics",
    "bind_log_user_id": ".logging",
    "build_prometheus_instrumentation": ".prometheus",
    "build_standard_logging_config": ".logging",
    "get_log_context": ".logging",
    "metric_label": ".metrics",
    "prepare_multiprocess_directory": ".prometheus",
    "request_context_middleware": ".logging",
    "setup_tracing": ".tracing",
}
_SUBMODULES = {
    "logging": ".logging",
    "metrics": ".metrics",
    "prometheus": ".prometheus",
    "tracing": ".tracing",
}


def __getattr__(name: str):
    module_name = _EXPORT_MODULES.get(name) or _SUBMODULES.get(name)
    if module_name is None:
        raise AttributeError(name)

    try:
        module = importlib.import_module(module_name, __name__)
    except ModuleNotFoundError as exc:  # pragma: no cover - runtime guard
        if (exc.name or "").startswith("opentelemetry"):
            raise ModuleNotFoundError(
                "Tracing helpers require the optional 'tracing' extra. "
                "Install with 'pip install service-toolkit[tracing]'."
            ) from exc
        raise

    if name in _SUBMODULES:
        value = module
    else:
        value = getattr(module, name)
    setattr(sys.modules[__name__], name, value)
    return value


def __dir__() -> list[str]:  # pragma: no cover - reflection helper
    return sorted(set(__all__))
