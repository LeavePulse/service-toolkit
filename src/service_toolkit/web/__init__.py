"""HTTP and Litestar-facing helpers."""

from __future__ import annotations

import importlib
import sys

__all__ = [
    "DEFAULT_JWT_EXCLUDE",
    "NATIVE_SOURCE",
    "ON_BEHALF_OF_HEADER",
    "ActorContext",
    "ActorRef",
    "ExpansionLoader",
    "HealthController",
    "JWTAuthIntegration",
    "ProjectionSpec",
    "RateLimitFailureMode",
    "ResponsePolicy",
    "SDK_SCHEMA_VERSION",
    "SdkLink",
    "build_shared_async_client",
    "close_shared_async_clients",
    "create_service_app",
    "current_actor",
    "default_openapi_render_plugins",
    "enforce_request_rate_limit",
    "extract_bearer_token",
    "extract_internal_token",
    "internal_token_matches",
    "intersect_perms_bits",
    "intersect_scopes",
    "normalize_locale_tag",
    "parse_on_behalf_of",
    "rate_limited_request",
    "request_projection",
    "reset_current_actor",
    "resolve_client_ip",
    "resolve_cors_origins",
    "resolve_locale_from_request",
    "sdk_operation",
    "set_current_actor",
    "stamp_sdk_hints",
    "with_projection",
]

_EXPORT_MODULES = {
    "DEFAULT_JWT_EXCLUDE": ".app_factory",
    "NATIVE_SOURCE": ".actor",
    "ON_BEHALF_OF_HEADER": ".actor",
    "ActorContext": ".actor",
    "ActorRef": ".actor",
    "current_actor": ".actor",
    "intersect_perms_bits": ".actor",
    "intersect_scopes": ".actor",
    "parse_on_behalf_of": ".actor",
    "reset_current_actor": ".actor",
    "set_current_actor": ".actor",
    "ExpansionLoader": ".projection",
    "HealthController": ".health",
    "JWTAuthIntegration": ".jwt_integration",
    "ProjectionSpec": ".projection",
    "RateLimitFailureMode": ".rate_limit",
    "ResponsePolicy": ".projection",
    "SDK_SCHEMA_VERSION": ".sdk_hints",
    "SdkLink": ".sdk_hints",
    "build_shared_async_client": ".http",
    "close_shared_async_clients": ".http",
    "create_service_app": ".app_factory",
    "default_openapi_render_plugins": ".openapi",
    "enforce_request_rate_limit": ".rate_limit",
    "extract_bearer_token": ".auth",
    "extract_internal_token": ".auth",
    "internal_token_matches": ".auth",
    "normalize_locale_tag": ".locale",
    "rate_limited_request": ".rate_limit",
    "request_projection": ".projection",
    "resolve_client_ip": ".request_ip",
    "resolve_cors_origins": ".cors",
    "resolve_locale_from_request": ".locale",
    "sdk_operation": ".sdk_hints",
    "stamp_sdk_hints": ".sdk_hints",
    "with_projection": ".projection_decorator",
}
_SUBMODULES = {
    "actor": ".actor",
    "app_factory": ".app_factory",
    "auth": ".auth",
    "cors": ".cors",
    "health": ".health",
    "http": ".http",
    "jwt_integration": ".jwt_integration",
    "locale": ".locale",
    "openapi": ".openapi",
    "projection": ".projection",
    "projection_decorator": ".projection_decorator",
    "rate_limit": ".rate_limit",
    "request_ip": ".request_ip",
    "sdk_hints": ".sdk_hints",
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
                "App factory and error helpers require the optional 'errors' extra. "
                "Install with 'pip install service-toolkit[errors]'."
            ) from exc
        if missing == "litestar":
            raise ModuleNotFoundError(
                "Web helpers require Litestar. "
                "Install with 'pip install litestar[standard]'."
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
