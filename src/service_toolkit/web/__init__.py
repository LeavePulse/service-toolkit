"""HTTP and Litestar-facing helpers."""

from __future__ import annotations

import importlib
import sys

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

_EXPORT_MODULES = {
    "DEFAULT_JWT_EXCLUDE": ".app_factory",
    "HealthController": ".health",
    "JWTAuthMiddleware": ".middleware",
    "RateLimitFailureMode": ".rate_limit",
    "build_shared_async_client": ".http",
    "close_shared_async_clients": ".http",
    "create_service_app": ".app_factory",
    "default_openapi_render_plugins": ".openapi",
    "enforce_request_rate_limit": ".rate_limit",
    "rate_limited_request": ".rate_limit",
    "resolve_client_ip": ".request_ip",
    "resolve_cors_origins": ".cors",
}
_SUBMODULES = {
    "app_factory": ".app_factory",
    "cors": ".cors",
    "health": ".health",
    "http": ".http",
    "middleware": ".middleware",
    "openapi": ".openapi",
    "rate_limit": ".rate_limit",
    "request_ip": ".request_ip",
}


def __getattr__(name: str):
    module_name = _EXPORT_MODULES.get(name) or _SUBMODULES.get(name)
    if module_name is None:
        raise AttributeError(name)

    try:
        module = importlib.import_module(module_name, __name__)
    except ModuleNotFoundError as exc:  # pragma: no cover - runtime guard
        missing = exc.name or ""
        if missing in {"awesome_errors", "awesome-errors"}:
            raise ModuleNotFoundError(
                "App factory helpers require the optional 'errors' extra. "
                "Install with 'pip install service-toolkit[errors]'."
            ) from exc
        if missing in {"jose", "msgspec"}:
            raise ModuleNotFoundError(
                "JWT middleware helpers require the optional 'auth' extra. "
                "Install with 'pip install service-toolkit[auth]'."
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
