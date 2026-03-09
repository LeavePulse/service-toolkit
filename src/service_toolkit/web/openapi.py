"""OpenAPI helper utilities shared across services."""

from __future__ import annotations

from litestar.openapi.plugins import (
    RedocRenderPlugin,
    ScalarRenderPlugin,
    StoplightRenderPlugin,
    SwaggerRenderPlugin,
)


def default_openapi_render_plugins():
    """Return the default OpenAPI UI render plugins used across services."""

    return [
        ScalarRenderPlugin(),
        SwaggerRenderPlugin(),
        RedocRenderPlugin(),
        StoplightRenderPlugin(),
    ]


__all__ = ["default_openapi_render_plugins"]
