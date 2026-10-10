"""OpenAPI schema for maps keyed by integers.

JSON object keys are strings, so ``dict[int, int]`` travels as
``{"1": 2}`` and msgspec decodes the keys back to ``int``. Litestar renders
such a field as a plain ``object`` with ``additionalProperties`` and drops the
key type, so a client generated from the schema types the map
``dict[str, V]`` and every reader converts keys by hand. This plugin keeps the
key type as ``propertyNames`` with an integer pattern, the one way JSON Schema
can say "these string keys are integers".
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, get_args, get_origin

from litestar.openapi.spec import OpenAPIType, Schema
from litestar.plugins import OpenAPISchemaPlugin
from litestar.typing import FieldDefinition

#: ``propertyNames`` of a map whose keys are integers on the wire.
INTEGER_KEYS = Schema(type=OpenAPIType.STRING, pattern=r"^-?[0-9]+$")


def _int_keyed(annotation: Any) -> tuple[Any, ...] | None:
    origin = get_origin(annotation)
    if (
        origin is None
        or not isinstance(origin, type)
        or not issubclass(origin, Mapping)
    ):
        return None
    args = get_args(annotation)
    if len(args) != 2 or args[0] is not int:
        return None
    return args


class IntegerKeyedMapPlugin(OpenAPISchemaPlugin):
    """Render ``dict[int, V]`` with its integer key type kept."""

    @staticmethod
    def is_plugin_supported_type(value: Any) -> bool:
        return _int_keyed(value) is not None

    def to_openapi_schema(
        self, field_definition: FieldDefinition, schema_creator: Any
    ) -> Schema:
        args = _int_keyed(field_definition.annotation)
        if args is None:
            return Schema(type=OpenAPIType.OBJECT)
        values = schema_creator.for_field_definition(
            FieldDefinition.from_annotation(args[1])
        )
        return Schema(
            type=OpenAPIType.OBJECT,
            additional_properties=values,
            property_names=INTEGER_KEYS,
        )


__all__ = ["INTEGER_KEYS", "IntegerKeyedMapPlugin"]
