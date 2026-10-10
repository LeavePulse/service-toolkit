"""An integer-keyed map keeps its key type in the schema."""

from __future__ import annotations

import msgspec
from litestar import Litestar, get
from litestar._openapi.plugin import OpenAPIPlugin

from service_toolkit.web.openapi_maps import IntegerKeyedMapPlugin


class Levels(msgspec.Struct, kw_only=True):
    by_branch: dict[int, int] = msgspec.field(default_factory=dict)
    by_name: dict[str, int] = msgspec.field(default_factory=dict)
    maybe: dict[int, str] | None = None


@get("/levels", sync_to_thread=False)
def levels() -> Levels:
    return Levels()


def _props() -> dict[str, object]:
    app = Litestar(route_handlers=[levels], plugins=[IntegerKeyedMapPlugin()])
    schema = app.plugins.get(OpenAPIPlugin).provide_openapi_schema()
    return schema["components"]["schemas"]["Levels"]["properties"]


def test_integer_keys_are_named() -> None:
    by_branch = _props()["by_branch"]
    assert by_branch["propertyNames"] == {"type": "string", "pattern": "^-?[0-9]+$"}
    assert by_branch["additionalProperties"] == {"type": "integer"}


def test_string_keys_are_left_alone() -> None:
    assert "propertyNames" not in _props()["by_name"]


def test_an_optional_map_keeps_its_key_type() -> None:
    variants = _props()["maybe"]["oneOf"]
    assert any(v.get("propertyNames") for v in variants)
