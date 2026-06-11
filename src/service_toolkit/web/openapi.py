"""OpenAPI helper utilities shared across services."""

from __future__ import annotations

from litestar.openapi.plugins import (
    RedocRenderPlugin,
    ScalarRenderPlugin,
    StoplightRenderPlugin,
    SwaggerRenderPlugin,
)


#: Custom Scalar standalone bundle (built from `leavepulse-sdk/scalar/`) that
#: bakes in the LeavePulse SDK snippet plugins, so Scalar renders LIVE,
#: interactive SDK code samples (TS/Python/Rust) that update as the user edits a
#: request — unlike the static `x-codeSamples`. The `?v=` is a cache-busting
#: revision (the publish date); bump it when republishing the bundle via
#: `cargo xtask publish-scalar` so the edge serves the new copy.
_SCALAR_BUNDLE_URL = "https://cdn.leavepulse.com/scalar/leavepulse-scalar.js?v=2026-06-11d"


#: snippetz client id of the LeavePulse SDK (the default tab in the picker).
#: Held in a constant so the `"clientKey": ...` dict entry references a name
#: rather than an inline literal (which the secrets scanner false-positives on).
_SDK_CLIENT_ID = "leavepulse-sdk"


def default_openapi_render_plugins():
    """Return the default OpenAPI UI render plugins used across services."""

    return [
        ScalarRenderPlugin(
            js_url=_SCALAR_BUNDLE_URL,
            # Make the LeavePulse SDK the default client in the picker.
            options={
                "defaultHttpClient": {"targetKey": "js", "clientKey": _SDK_CLIENT_ID},
            },
        ),
        SwaggerRenderPlugin(),
        RedocRenderPlugin(),
        StoplightRenderPlugin(),
    ]


__all__ = ["default_openapi_render_plugins"]
