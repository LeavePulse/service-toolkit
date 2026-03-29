"""HTTP and Litestar-facing helpers."""

from __future__ import annotations

import importlib
import sys

__all__ = [
    "CurrentUser",
    "DEFAULT_JWT_EXCLUDE",
    "ExpansionLoader",
    "HealthController",
    "JWTAuthMiddleware",
    "ProjectionSpec",
    "RateLimitFailureMode",
    "ResponsePolicy",
    "build_shared_async_client",
    "close_shared_async_clients",
    "current_user",
    "current_user_dependency",
    "create_service_app",
    "default_openapi_render_plugins",
    "enforce_request_rate_limit",
    "provide_current_user",
    "rate_limited_request",
    "require_user",
    "request_projection",
    "resolve_client_ip",
    "resolve_cors_origins",
    "with_projection",
]

_EXPORT_MODULES = {
    "CurrentUser": ".auth",
    "DEFAULT_JWT_EXCLUDE": ".app_factory",
    "ExpansionLoader": ".projection",
    "HealthController": ".health",
    "JWTAuthMiddleware": ".middleware",
    "ProjectionSpec": ".projection",
    "RateLimitFailureMode": ".rate_limit",
    "ResponsePolicy": ".projection",
    "build_shared_async_client": ".http",
    "close_shared_async_clients": ".http",
    "current_user": ".auth",
    "current_user_dependency": ".auth",
    "create_service_app": ".app_factory",
    "default_openapi_render_plugins": ".openapi",
    "enforce_request_rate_limit": ".rate_limit",
    "provide_current_user": ".auth",
    "rate_limited_request": ".rate_limit",
    "require_user": ".auth",
    "request_projection": ".projection",
    "resolve_client_ip": ".request_ip",
    "resolve_cors_origins": ".cors",
    "with_projection": ".projection_decorator",
}
_SUBMODULES = {
    "app_factory": ".app_factory",
    "auth": ".auth",
    "cors": ".cors",
    "health": ".health",
    "http": ".http",
    "middleware": ".middleware",
    "openapi": ".openapi",
    "projection": ".projection",
    "projection_decorator": ".projection_decorator",
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
                "App factory and web auth helpers require the optional 'errors' extra. "
                "Install with 'pip install service-toolkit[errors]'."
            ) from exc
        if missing == "litestar":
            raise ModuleNotFoundError(
                "Web helpers require Litestar. "
                "Install with 'pip install litestar[standard]'."
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
