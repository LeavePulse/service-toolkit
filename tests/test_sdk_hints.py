"""Which handlers the SDK sees.

A handler joins the SDK by carrying ``x-sdk-*`` keys in the rendered schema.
``@sdk_operation`` always puts them there; ``explicit_operations`` also counts
an ``operation_id`` the author wrote as that decision, so a service whose SDK is
a flat list of procedures does not repeat an empty decorator over every route.
"""

from __future__ import annotations

from typing import Any

from litestar import Litestar, get, post
from litestar._openapi.plugin import OpenAPIPlugin

from service_toolkit.web.sdk_hints import (
    SDK_SCHEMA_VERSION,
    sdk_operation,
    stamp_sdk_hints,
)


@get("/named", operation_id="things.named", sync_to_thread=False)
def named() -> dict[str, int]:
    return {}


@get("/anonymous", sync_to_thread=False)
def anonymous() -> dict[str, int]:
    return {}


@sdk_operation(paginated=True)
@post("/hinted", operation_id="things.hinted", sync_to_thread=False)
def hinted() -> list[int]:
    return []


def _operations(explicit: bool) -> dict[str, dict[str, Any]]:
    app = Litestar(route_handlers=[named, anonymous, hinted])
    stamp_sdk_hints(app, explicit_operations=explicit)
    schema = app.plugins.get(OpenAPIPlugin).provide_openapi_schema()
    return {path: next(iter(item.values())) for path, item in schema["paths"].items()}


def test_only_decorated_handlers_join_by_default() -> None:
    ops = _operations(explicit=False)
    assert "x-sdk-schema-version" not in ops["/named"]
    assert "x-sdk-schema-version" not in ops["/anonymous"]
    assert ops["/hinted"]["x-sdk-paginated"] is True


def test_an_explicit_operation_id_joins_as_a_procedure() -> None:
    ops = _operations(explicit=True)
    assert ops["/named"]["x-sdk-schema-version"] == SDK_SCHEMA_VERSION
    assert not any(
        k.startswith("x-sdk-") and k != "x-sdk-schema-version" for k in ops["/named"]
    )


def test_a_generated_operation_id_stays_out() -> None:
    assert "x-sdk-schema-version" not in _operations(explicit=True)["/anonymous"]


def test_the_decorator_still_carries_its_hints() -> None:
    assert _operations(explicit=True)["/hinted"]["x-sdk-paginated"] is True
