"""SDK generation hints for OpenAPI operations.

This module lets controllers annotate operations with the metadata a
resource-object SDK generator needs (see platform-api RFC 0001). The hints
are attached in code via :func:`sdk_operation`, then stamped onto the final
OpenAPI document as ``x-sdk-*`` extension fields by :func:`stamp_sdk_hints`.

Litestar's ``Operation`` spec object has no ``extensions`` field, so the
hints cannot be set on the operation object itself. Instead the decorator
stores them in ``handler.opt['x_sdk']`` and the stamper injects them into the
rendered ``paths`` dict, matched by ``operationId``. The cache-invalidation
dance mirrors ``app_factory._stamp_handler_security``.

Only the keys defined in RFC 0001 §3.1 are emitted. The set is intentionally
small; adding a key is an RFC amendment, not a code change here.

``sdk_operation`` MUST be applied *above* the route decorator so it
receives the constructed handler (the route decorator builds the handler
from the function and would discard hints left on the bare function).

Usage::

    from service_toolkit.web.sdk_hints import sdk_operation

    class ProjectsController(Controller):
        @sdk_operation(resource="Project")
        @get("/{project_id:int}", operation_id="project.get")
        async def get_project(self, project_id: int) -> Project: ...

        @sdk_operation(resource="Project", action_of="Project", capability="rename")
        @post(
            "/{project_id:int}/actions/rename",
            operation_id="project.rename",
        )
        async def rename_project(self, project_id: int, data: RenameRequest) -> Project: ...
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

    from litestar import Litestar

#: Version of the x-sdk hint schema (RFC 0001 §3.1). Bumped when the hint
#: schema itself changes, independent of the data contract.
#: v2 added ``x-sdk-returns`` (an operation yielding instances of another
#: resource, e.g. ``me.sessions.list`` returning ``Session``).
#: v3 added ``x-sdk-data-root`` (response wraps the resource under a field,
#: e.g. ``ProjectDetail = {project: {...}, servers: [...]}``).
SDK_SCHEMA_VERSION = 3

#: Key under which raw hints live in ``handler.opt`` before stamping.
_OPT_KEY = "x_sdk"


class SdkLink:
    """A typed relation from one resource to another (RFC 0001 §3.2).

    A link references a *resource*, never an ``operationId`` — binding to an
    operation id would break if that operation is later renamed or split. The
    generator's IR resolves which source operation actually fetches the data.
    """

    __slots__ = ("name", "target_resource", "pick", "cached", "batchable")

    def __init__(
        self,
        name: str,
        target_resource: str,
        *,
        pick: str | None = None,
        cached: bool = False,
        batchable: bool = False,
    ) -> None:
        self.name = name
        self.target_resource = target_resource
        self.pick = pick
        self.cached = cached
        self.batchable = batchable

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": self.name,
            "target_resource": self.target_resource,
        }
        if self.pick is not None:
            data["pick"] = self.pick
        if self.cached:
            data["cached"] = True
        if self.batchable:
            data["batchable"] = True
        return data


def sdk_operation(
    *,
    resource: str | None = None,
    action_of: str | None = None,
    paginated: bool = False,
    raw: bool = False,
    capability: str | None = None,
    batchable: bool = False,
    returns: str | None = None,
    data_root: str | None = None,
    links: list[SdkLink] | None = None,
):
    """Attach SDK generation hints to a Litestar route handler.

    Apply *above* the route decorator (``@get``/``@post``) so it receives the
    constructed handler — applied below, it would only annotate the bare
    function, which the route decorator then discards. The ``operation_id``
    itself is passed to the route decorator (it is a native OpenAPI field);
    this decorator carries only the ``x-sdk-*`` metadata.

    Args mirror RFC 0001 §3.1:
        resource: resource class this operation belongs to.
        action_of: operation is an action on an instance of this resource
            (becomes an instance method, e.g. ``project.rename()``).
        paginated: list operation → async iterator / ``Page<T>`` in the SDK.
        raw: response body is opaque → SDK returns an untyped mapping.
        capability: static capability name (e.g. ``rename``) → ``can_*``.
        batchable: eligible for batch loading (guard against N+1).
        returns: this operation yields instances of another resource
            (e.g. ``me.sessions.list`` returns ``Session``); the items are
            hydrated as that resource, which owns its own actions.
        data_root: the response wraps the resource under this field; the SDK
            resource binds to that inner object (e.g. ``project`` in
            ``{project: {...}, servers: [...]}``).
        links: typed relations to other resources.
    """
    hints: dict[str, Any] = {"schema_version": SDK_SCHEMA_VERSION}
    if resource is not None:
        hints["resource"] = resource
    if action_of is not None:
        hints["action_of"] = action_of
    if paginated:
        hints["paginated"] = True
    if raw:
        hints["raw"] = True
    if capability is not None:
        hints["capability"] = capability
    if batchable:
        hints["batchable"] = True
    if returns is not None:
        hints["returns"] = returns
    if data_root is not None:
        hints["data_root"] = data_root
    if links:
        hints["links"] = [link.to_dict() for link in links]

    def decorate(handler):
        from litestar.handlers import BaseRouteHandler

        if not isinstance(handler, BaseRouteHandler):
            msg = (
                "@sdk_operation must be applied ABOVE the route decorator "
                "(@get/@post/...), so it receives the route handler rather "
                "than the bare function. Hints set on a plain function are "
                "silently discarded when the route decorator builds the "
                "handler."
            )
            raise TypeError(msg)
        existing = dict(getattr(handler, "opt", {}) or {})
        existing[_OPT_KEY] = hints
        handler.opt = existing
        return handler

    return decorate


def _hints_to_extensions(hints: Mapping[str, Any]) -> dict[str, Any]:
    """Translate stored hints into ``x-sdk-*`` OpenAPI extension fields."""
    extensions: dict[str, Any] = {
        "x-sdk-schema-version": hints.get("schema_version", SDK_SCHEMA_VERSION),
    }
    if "resource" in hints:
        extensions["x-sdk-resource"] = hints["resource"]
    if "action_of" in hints:
        extensions["x-sdk-action-of"] = hints["action_of"]
    if hints.get("paginated"):
        extensions["x-sdk-paginated"] = True
    if hints.get("raw"):
        extensions["x-sdk-raw"] = True
    if "capability" in hints:
        extensions["x-sdk-capability"] = hints["capability"]
    if hints.get("batchable"):
        extensions["x-sdk-batchable"] = True
    if "returns" in hints:
        extensions["x-sdk-returns"] = hints["returns"]
    if "data_root" in hints:
        extensions["x-sdk-data-root"] = hints["data_root"]
    if "links" in hints:
        extensions["x-sdk-link"] = hints["links"]
    return extensions


def stamp_sdk_hints(app: Litestar) -> None:
    """Inject ``x-sdk-*`` extensions into the rendered OpenAPI document.

    Walks registered HTTP handlers, reads the hints stored in
    ``handler.opt['x_sdk']``, and writes the corresponding ``x-sdk-*`` keys
    into the matching operation object of the OpenAPI ``paths`` dict, matched
    by ``operationId``. Safe to call once after app construction.
    """
    from litestar._openapi.plugin import OpenAPIPlugin
    from litestar.handlers import HTTPRouteHandler

    hints_by_operation_id: dict[str, dict[str, Any]] = {}
    skip_methods = {"OPTIONS", "HEAD"}
    for route in app.routes:
        for handler in getattr(route, "route_handlers", ()):
            if not isinstance(handler, HTTPRouteHandler):
                continue
            if skip_methods >= set(handler.http_methods):
                continue
            hints = (handler.opt or {}).get(_OPT_KEY)
            if not hints:
                continue
            operation_id = handler.operation_id
            if not isinstance(operation_id, str) or not operation_id:
                continue
            hints_by_operation_id[operation_id] = _hints_to_extensions(hints)

    if not hints_by_operation_id:
        return

    try:
        plugin = app.plugins.get(OpenAPIPlugin)
    except KeyError:
        return

    schema = plugin.provide_openapi_schema()
    for path_item in (schema.get("paths") or {}).values():
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            operation_id = operation.get("operationId")
            if not isinstance(operation_id, str):
                continue
            extensions = hints_by_operation_id.get(operation_id)
            if extensions:
                operation.update(extensions)


__all__ = ["SDK_SCHEMA_VERSION", "SdkLink", "sdk_operation", "stamp_sdk_hints"]
