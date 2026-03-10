"""HTTP and Litestar-facing helpers."""

from __future__ import annotations

import importlib
import sys

from .cors import resolve_cors_origins
from .health import HealthController
from .http import build_shared_async_client, close_shared_async_clients
from .rate_limit import (
    RateLimitFailureMode,
    enforce_request_rate_limit,
    rate_limited_request,
)
from .request_ip import resolve_client_ip

__all__ = [
    "DEFAULT_JWT_EXCLUDE",
    "HealthController",
    "JWTAuthMiddleware",
    "RateLimitFailureMode",
    "build_shared_async_client",
    "close_shared_async_clients",
    "create_service_app",
    "default_openapi_render_plugins",
    "enforce_request_rate_limit",
    "rate_limited_request",
    "resolve_client_ip",
    "resolve_cors_origins",
]

_OPTIONAL_EXPORT_MODULES = {
    "DEFAULT_JWT_EXCLUDE": ".app_factory",
    "JWTAuthMiddleware": ".middleware",
    "create_service_app": ".app_factory",
    "default_openapi_render_plugins": ".openapi",
}


def __getattr__(name: str):
    module_name = _OPTIONAL_EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(name)

    try:
        module = importlib.import_module(module_name, __name__)
    except ModuleNotFoundError as exc:  # pragma: no cover - runtime guard
        if exc.name == "awesome_errors":
            raise ModuleNotFoundError(
                "App factory helpers require the optional 'errors' extra. "
                "Install with 'pip install service-toolkit[errors]'."
            ) from exc
        raise

    value = getattr(module, name)
    setattr(sys.modules[__name__], name, value)
    return value


def __dir__() -> list[str]:  # pragma: no cover - reflection helper
    return sorted(set(__all__))
