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
        # Provider-owned auth integration
        auth_integration=auth_integration,
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
from .etag import etag_middleware
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

    from .jwt_integration import JWTAuthIntegration

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

#: Security requirement applied per-operation to authenticated routes.
_BEARER_SECURITY_REQUIREMENT = [{"BearerAuth": ["openid"]}]


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
    # Auth (optional — omit auth_integration to skip JWT middleware entirely)
    auth_integration: JWTAuthIntegration | None = None,
    jwt_exclude_patterns: Sequence[str] = (),
    # SQLAlchemy (optional)
    sqlalchemy_config: SQLAlchemyAsyncConfig | None = None,
    # Tracing
    instrument_redis: bool = True,
    instrument_sqlalchemy: bool = True,
    # Prometheus
    prometheus_route: str = "/metrics",
    # Logging
    logging_config: BaseLoggingConfig | None = None,
    # Global DI
    dependencies: Mapping[str, Any] | None = None,
    # Error handling
    custom_translations: Mapping[str, Mapping[str, str]] | None = None,
    # Lifecycle
    on_startup: Sequence[LifeSpanHandler] = (),
    on_shutdown: Sequence[LifeSpanHandler] = (),
    # Escape hatch for extra middleware. ``DefineMiddleware`` works for
    # classic middleware classes (Litestar will instantiate them with
    # ``app=...``); ASGI-style middleware instances (subclassing
    # ``litestar.middleware.base.ASGIMiddleware``) are passed through
    # to Litestar verbatim.
    extra_middleware: Sequence[Any] = (),
    # Extra plugins
    extra_plugins: Sequence[Any] = (),
    # Hooks run over the rendered OpenAPI document (the live ``/openapi.json``)
    # after SDK hints are stamped. Each receives the document dict and mutates
    # it in place — used by the contract-owning service to reshape the published
    # schema (e.g. lift Snowflake ids into a named component). Generic services
    # leave this empty.
    openapi_postprocess: Sequence[Callable[[dict[str, Any]], None]] = (),
    # Conditional-request support: stamp an ETag on cacheable GET responses and
    # answer a matching If-None-Match with 304. Live endpoints opt out per-route
    # via ``Cache-Control: no-store``. Disable for services with no cacheable GET.
    enable_etag: bool = True,
) -> Litestar:
    """Create a fully-configured Litestar application.

    This handles all the boilerplate: error handling, OpenAPI docs, CORS,
    Prometheus metrics, OpenTelemetry tracing, provider-owned auth middleware,
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

    # ── Auth integration ─────────────────────────────────────────────
    if auth_integration is not None:
        resolved_dependencies = {
            **dict(auth_integration.dependencies),
            **dict(dependencies or {}),
        }
    else:
        resolved_dependencies = dict(dependencies or {})

    # ── Middleware stack ──────────────────────────────────────────────
    middleware: list[DefineMiddleware] = []
    if otel_middleware is not None:
        middleware.append(DefineMiddleware(otel_middleware))
    middleware.append(DefineMiddleware(request_context_middleware))
    # ETag sits high in the stack so it sees the fully-rendered response body
    # (after handlers + serialization) yet inside tracing/context.
    if enable_etag:
        middleware.append(DefineMiddleware(etag_middleware))
    middleware.append(DefineMiddleware(PrometheusMiddleware))

    if auth_integration is not None:
        exclude = list(DEFAULT_JWT_EXCLUDE) + list(jwt_exclude_patterns)
        jwt_kwargs: dict[str, Any] = {
            "jwt_verifier": auth_integration.jwt_verifier,
            "exclude": exclude,
            "exclude_http_methods": ["OPTIONS"],
        }
        jwt_kwargs.update(auth_integration.middleware_kwargs)
        middleware.append(
            DefineMiddleware(auth_integration.middleware_class, **jwt_kwargs)
        )

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
    if auth_integration is not None:
        # Declare the scheme, but DON'T set a document-level ``security``.
        # A global requirement applies to every operation and cannot be
        # cleared per-route (Litestar #4016: route-level ``security=[]`` is
        # dropped by ``resolve_security() or None``), so public routes would
        # wrongly render as "auth required" in Scalar/Swagger. Instead we
        # stamp the requirement onto each authenticated handler below, leaving
        # ``exclude_from_auth`` routes genuinely open in the schema.
        openapi_kwargs["components"] = Components(
            security_schemes={"BearerAuth": _BEARER_SECURITY_SCHEME}
        )
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
        dependencies=resolved_dependencies or None,
    )

    apply_problem_details(app, service_name=service_name)

    if auth_integration is not None:
        _stamp_handler_security(app)

    from .sdk_hints import stamp_sdk_hints

    stamp_sdk_hints(app)

    if openapi_postprocess:
        _apply_openapi_postprocess(app, openapi_postprocess)

    return app


def _apply_openapi_postprocess(
    app: Litestar,
    hooks: Sequence[Callable[[dict[str, Any]], None]],
) -> None:
    """Run each hook over the rendered OpenAPI document, in place.

    Mutates the same cached dict that backs ``/openapi.json`` (the one
    ``stamp_sdk_hints`` already writes to), so changes appear in both the live
    schema endpoint and the contract snapshot.
    """
    from litestar._openapi.plugin import OpenAPIPlugin

    try:
        plugin = app.plugins.get(OpenAPIPlugin)
    except KeyError:
        return
    schema = plugin.provide_openapi_schema()
    for hook in hooks:
        hook(schema)


def _stamp_handler_security(app: Litestar) -> None:
    """Apply the bearer requirement to every authenticated route handler.

    Walks the registered HTTP handlers and sets an operation-level
    ``security`` on each one that is *not* marked ``exclude_from_auth``.
    Public handlers are left untouched so they render without an auth
    requirement in the OpenAPI document. This is the inverse of a global
    requirement and side-steps Litestar #4016 (route-level ``security=[]``
    being dropped), since we never declare a document-level ``security``.
    """
    from litestar._openapi.plugin import OpenAPIPlugin
    from litestar.handlers import HTTPRouteHandler
    from litestar.types import Empty

    skip_methods = {"OPTIONS", "HEAD"}
    for route in app.routes:
        for handler in getattr(route, "route_handlers", ()):
            if not isinstance(handler, HTTPRouteHandler):
                continue
            # Auto-generated CORS/HEAD handlers carry no real operation.
            if skip_methods >= set(handler.http_methods):
                continue
            if handler.opt.get("exclude_from_auth"):
                continue
            handler.security = list(_BEARER_SECURITY_REQUIREMENT)
            # Drop any lazily-cached resolution so the new value is picked up.
            handler._resolved_security = Empty

    # The OpenAPI document may already have been built (e.g. during app init)
    # before this stamping ran. Invalidate the plugin cache so the schema is
    # regenerated with the per-operation security requirements in place.
    try:
        plugin = app.plugins.get(OpenAPIPlugin)
    except KeyError:
        return
    plugin._openapi = None
    plugin._openapi_schema = None


__all__ = ["DEFAULT_JWT_EXCLUDE", "create_service_app"]
