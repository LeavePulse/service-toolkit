"""Litestar application factory for LeavePulse services.

Eliminates the duplicated boilerplate found in every service ``main.py``.

Usage::

    from service_toolkit.web.app_factory import create_service_app

    app = create_service_app(
        service_name=settings.service_name,
        debug=settings.debug,
        openapi_title="Billing Service API",
        openapi_description="Products, checkout and subscriptions.",
        route_handlers=[BillingController, MonobankWebhookController],
        cors_allow_origins=settings.cors_allow_origins,
        cors_allow_origins_debug=settings.cors_allow_origins_debug,
        # JWT auth
        auth_settings=settings.auth,
        jwt_exclude_patterns=[r"^/billing/providers/monobank/webhook$"],
        # SQLAlchemy
        sqlalchemy_config=sqlalchemy_config,
        # Lifecycle
        on_startup=[startup_billing_events],
        on_shutdown=[shutdown_billing_events],
    )
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeAlias, cast

from awesome_errors import ErrorResponseFormat
from litestar import Litestar
from litestar.config.cors import CORSConfig
from litestar.middleware.base import DefineMiddleware
from litestar.openapi.config import OpenAPIConfig
from litestar.openapi.spec import Components, SecurityScheme

from ..errors.awesome_errors import (
    apply_problem_details,
    build_error_translator_with_defaults,
    build_standard_exception_handlers,
)
from .cors import resolve_cors_origins
from .health import HealthController
from ..observability.logging import (
    build_standard_logging_config,
    request_context_middleware,
)
from .openapi import default_openapi_render_plugins
from ..observability.prometheus import build_prometheus_instrumentation

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from advanced_alchemy.extensions.litestar import SQLAlchemyAsyncConfig
    from litestar.logging.config import BaseLoggingConfig
    from litestar.types import ControllerRouterHandler, ExceptionHandlersMap

    from ..settings.config import AuthSettings

    LifeSpanHandler: TypeAlias = Callable[..., object]


#: Default URL patterns excluded from JWT authentication.
DEFAULT_JWT_EXCLUDE = [
    r"^/health($|/.*)",
    r"^/docs($|/.*)",
    r"^/metrics($|/.*)",
]

_BEARER_SECURITY_SCHEME = SecurityScheme(
    name="BearerAuth",
    type="http",
    scheme="bearer",
    bearer_format="JWT",
    security_scheme_in="header",
    description="Enter JWT in the format 'Bearer <token>'.",
)


def create_service_app(
    *,
    service_name: str,
    debug: bool = False,
    # OpenAPI
    openapi_title: str,
    openapi_description: str = "",
    openapi_version: str = "1.0.0",
    # Routes
    route_handlers: Sequence[ControllerRouterHandler],
    # CORS
    cors_allow_origins: Sequence[str] = (),
    cors_allow_origins_debug: Sequence[str] = (),
    cors_allow_methods: Sequence[str] = (
        "GET",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
        "OPTIONS",
    ),
    # Auth (optional — omit auth_settings to skip JWT middleware entirely)
    auth_settings: AuthSettings | None = None,
    jwt_exclude_patterns: Sequence[str] = (),
    jwt_require_auth: bool = False,
    jwt_middleware_class: type | None = None,
    jwt_middleware_kwargs: dict[str, Any] | None = None,
    # SQLAlchemy (optional)
    sqlalchemy_config: SQLAlchemyAsyncConfig | None = None,
    # Tracing
    instrument_redis: bool = True,
    instrument_sqlalchemy: bool = True,
    # Prometheus
    prometheus_route: str = "/metrics",
    # Logging
    logging_config: BaseLoggingConfig | None = None,
    # Error handling
    custom_translations: Mapping[str, Mapping[str, str]] | None = None,
    # Lifecycle
    on_startup: Sequence[LifeSpanHandler] = (),
    on_shutdown: Sequence[LifeSpanHandler] = (),
    # Escape hatch for extra middleware
    extra_middleware: Sequence[DefineMiddleware] = (),
    # Extra plugins
    extra_plugins: Sequence[Any] = (),
) -> Litestar:
    """Create a fully-configured Litestar application.

    This handles all the boilerplate: error handling, OpenAPI docs,
    CORS, Prometheus metrics, OpenTelemetry tracing, JWT auth middleware,
    SQLAlchemy plugin, and health endpoints.
    """

    # ── Tracing ──────────────────────────────────────────────────────
    from ..observability.tracing import setup_tracing

    otel_middleware = setup_tracing(
        service_name=service_name,
        instrument_redis=instrument_redis,
        instrument_sqlalchemy=instrument_sqlalchemy,
    )

    # ── Slow-query logging ───────────────────────────────────────────
    if sqlalchemy_config is not None:
        from ..db import install_slow_query_logging

        install_slow_query_logging(service_name=service_name)

    # ── Error handling ───────────────────────────────────────────────
    error_translator = build_error_translator_with_defaults(
        service_name=service_name,
        custom_translations=custom_translations,
    )
    exception_handlers = cast(
        "ExceptionHandlersMap",
        build_standard_exception_handlers(
            service_name=service_name,
            translator=error_translator,
            debug=debug,
            response_format=ErrorResponseFormat.RFC7807,
        ),
    )

    # ── Prometheus ───────────────────────────────────────────────────
    PrometheusMiddleware, metrics_endpoint = build_prometheus_instrumentation(
        service_name=service_name,
        route=prometheus_route,
    )

    # ── JWT Verifier ─────────────────────────────────────────────────
    jwt_verifier = None
    if auth_settings is not None:
        from ..auth import build_shared_jwt_verifier

        jwt_verifier = build_shared_jwt_verifier(
            jwks_url=auth_settings.resolved_jwks_url,
            jwks_ttl_seconds=int(auth_settings.jwks_cache_ttl_seconds),
            http_timeout_seconds=float(auth_settings.http_timeout_seconds),
            issuer=auth_settings.issuer,
            audience=auth_settings.audience,
            introspect_url=auth_settings.resolved_introspect_url,
        )

    # ── Middleware stack ──────────────────────────────────────────────
    middleware: list[DefineMiddleware] = []
    if otel_middleware is not None:
        middleware.append(DefineMiddleware(otel_middleware))
    middleware.append(DefineMiddleware(request_context_middleware))
    middleware.append(DefineMiddleware(PrometheusMiddleware))

    if jwt_verifier is not None:
        jwt_cls = jwt_middleware_class
        if jwt_cls is None:
            from .middleware import JWTAuthMiddleware

            jwt_cls = JWTAuthMiddleware

        exclude = list(DEFAULT_JWT_EXCLUDE) + list(jwt_exclude_patterns)
        jwt_kwargs: dict[str, Any] = {
            "jwt_verifier": jwt_verifier,
            "exclude": exclude,
            "exclude_http_methods": ["OPTIONS"],
        }
        if jwt_middleware_class is None:
            jwt_kwargs["require_auth"] = jwt_require_auth
        if jwt_middleware_kwargs:
            jwt_kwargs.update(jwt_middleware_kwargs)
        middleware.append(DefineMiddleware(jwt_cls, **jwt_kwargs))

    middleware.extend(extra_middleware)

    # ── Route handlers ───────────────────────────────────────────────
    handlers: list[ControllerRouterHandler] = [HealthController, metrics_endpoint]
    handlers.extend(route_handlers)

    # ── Plugins ──────────────────────────────────────────────────────
    plugins: list[Any] = []
    if sqlalchemy_config is not None:
        from advanced_alchemy.extensions.litestar import SQLAlchemyInitPlugin

        plugins.append(SQLAlchemyInitPlugin(config=sqlalchemy_config))
    plugins.extend(extra_plugins)

    # ── OpenAPI ──────────────────────────────────────────────────────
    openapi_kwargs: dict[str, Any] = {
        "title": openapi_title,
        "version": openapi_version,
        "description": openapi_description,
        "render_plugins": default_openapi_render_plugins(),
        "path": "/docs",
    }
    if jwt_verifier is not None:
        openapi_kwargs["components"] = Components(
            security_schemes={"BearerAuth": _BEARER_SECURITY_SCHEME}
        )
        openapi_kwargs["security"] = [{"BearerAuth": ["openid"]}]
    openapi_config = OpenAPIConfig(**openapi_kwargs)

    # ── CORS ─────────────────────────────────────────────────────────
    cors_config = CORSConfig(
        allow_origins=resolve_cors_origins(
            debug=debug,
            allow_origins=cors_allow_origins,
            allow_origins_debug=cors_allow_origins_debug,
        ),
        max_age=0 if debug else 86400,
        allow_methods=cast("Any", list(cors_allow_methods)),
        allow_headers=["*"],
        allow_credentials=True,
    )

    # ── Build app ────────────────────────────────────────────────────
    app = Litestar(
        route_handlers=handlers,
        plugins=plugins,
        openapi_config=openapi_config,
        logging_config=logging_config or build_standard_logging_config(),
        debug=debug,
        on_startup=list(on_startup),
        on_shutdown=list(on_shutdown),
        middleware=middleware,
        cors_config=cors_config,
        exception_handlers=exception_handlers,
    )

    apply_problem_details(app, service_name=service_name)

    return app


__all__ = ["DEFAULT_JWT_EXCLUDE", "create_service_app"]
