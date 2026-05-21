"""Client-side gRPC call helpers.

Mirrors the server-side ``servicer.grpc_error_handler`` direction: takes
``grpc.aio.AioRpcError`` raised by a unary call and translates the status
code back into the corresponding ``awesome-errors`` exception so callers
(controllers, mappers, other services) see one consistent error surface.

Usage::

    resp = await grpc_call(
        _STUB.GetServer,
        catalog_pb2.GetServerRequest(server_id=server_id),
        timeout=5.0,
        resource="server",
        resource_id=server_id,
    )
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from typing import Any, TypeVar

import grpc

from awesome_errors import (
    AuthPermissionDeniedError,
    AuthRequiredError,
    InvalidInputError,
    ResourceNotFoundError,
)

_T = TypeVar("_T")


# gRPC StatusCode → awesome-errors exception factory.
# Each factory accepts (detail, resource, resource_id) and returns an exception.
_DEFAULT_TRANSLATION: dict[grpc.StatusCode, Any] = {
    grpc.StatusCode.NOT_FOUND: lambda detail, resource, rid: ResourceNotFoundError(
        resource or "resource", str(rid) if rid is not None else None
    ),
    grpc.StatusCode.INVALID_ARGUMENT: lambda detail, resource, rid: InvalidInputError(
        detail or "Invalid argument"
    ),
    grpc.StatusCode.UNAUTHENTICATED: lambda detail, resource, rid: AuthRequiredError(
        detail or "Authentication required"
    ),
    grpc.StatusCode.PERMISSION_DENIED: lambda detail, resource, rid: AuthPermissionDeniedError(
        detail or "Insufficient permissions"
    ),
    grpc.StatusCode.ALREADY_EXISTS: lambda detail, resource, rid: InvalidInputError(
        detail or "Resource already exists"
    ),
}


def translate_grpc_error(
    exc: grpc.aio.AioRpcError,
    *,
    resource: str | None = None,
    resource_id: object = None,
    extra: Mapping[grpc.StatusCode, Any] | None = None,
) -> Exception:
    """Map a gRPC client error to the corresponding awesome-errors exception.

    ``extra`` lets a caller override or extend the default table for
    service-specific cases (e.g. mapping ``FAILED_PRECONDITION`` to a
    custom validation exception).
    """
    code = exc.code()
    detail = exc.details() or ""
    factory = None
    if extra is not None and code in extra:
        factory = extra[code]
    if factory is None:
        factory = _DEFAULT_TRANSLATION.get(code)
    if factory is not None:
        return factory(detail, resource, resource_id)
    msg = detail or f"upstream rejected the request ({code.name})"
    return InvalidInputError(msg)


async def grpc_call(
    method: Any,
    request: Any,
    *,
    timeout: float,
    resource: str | None = None,
    resource_id: object = None,
    extra_errors: Mapping[grpc.StatusCode, Any] | None = None,
) -> Any:
    """Invoke a unary gRPC stub method with standardised error translation.

    The translated exception is raised with the original ``AioRpcError``
    as its ``__cause__`` so tracebacks remain debuggable.
    """
    try:
        return await method(request, timeout=timeout)
    except grpc.aio.AioRpcError as exc:
        raise translate_grpc_error(
            exc,
            resource=resource,
            resource_id=resource_id,
            extra=extra_errors,
        ) from exc


# ---------------------------------------------------------------------------
# proto3 optional-field helpers.
# ---------------------------------------------------------------------------

def optional_str(message: Any, field: str) -> str | None:
    """Return ``str(getattr(message, field))`` when the optional field is set."""
    if not message.HasField(field):
        return None
    return str(getattr(message, field))


def optional_int(message: Any, field: str) -> int | None:
    """Return ``int(getattr(message, field))`` when the optional field is set."""
    if not message.HasField(field):
        return None
    return int(getattr(message, field))


def optional_bool(message: Any, field: str) -> bool | None:
    """Return ``bool(getattr(message, field))`` when the optional field is set."""
    if not message.HasField(field):
        return None
    return bool(getattr(message, field))


def optional_float(message: Any, field: str) -> float | None:
    """Return ``float(getattr(message, field))`` when the optional field is set."""
    if not message.HasField(field):
        return None
    return float(getattr(message, field))


def optional_dt(message: Any, field: str) -> datetime | None:
    """Parse an ISO-8601 datetime field, returning ``None`` for absent or unparseable values.

    Services publish timestamps as ISO-8601 strings (proto3 ``string``) rather
    than ``google.protobuf.Timestamp`` so the wire shape stays stable across
    language ecosystems. This helper centralises the parse + None-fallback
    pattern that every consumer was open-coding.
    """
    raw = optional_str(message, field)
    if raw is None or not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def optional_str_from_int(message: Any, field: str) -> str | None:
    """Stringified ``optional_int`` for proto fields whose public model exposes them as ``str``.

    Used when the wire shape is ``optional int64`` (e.g. ``owner_id``) but
    the public contract carries it as a string (typically because the
    public id space is larger than 2^63 or uses a different encoding).
    """
    if not message.HasField(field):
        return None
    return str(getattr(message, field))


# ---------------------------------------------------------------------------
# PATCH-style request builders.
# ---------------------------------------------------------------------------

_UNSET = object()


def apply_optional_fields(request: Any, /, **fields: object) -> None:
    """Set scalar proto fields only when the caller passed a non-None value.

    Used to translate Python ``None``-means-"no change" semantics into proto3
    optional-field semantics — kwargs that are ``None`` are skipped, others
    are coerced into the matching proto type implicitly by protobuf.

    Example::

        apply_optional_fields(
            request,
            is_public=is_public,
            sort_order=sort_order,
            member_state=member_state,
        )
    """
    for name, value in fields.items():
        if value is None or value is _UNSET:
            continue
        setattr(request, name, value)


def present_fields(
    *,
    unset_type: type[Any] | tuple[type[Any], ...] | None = None,
    none_value: object = _UNSET,
    coerce: Callable[[object], object] | None = None,
    **fields: object,
) -> dict[str, object]:
    """Return fields explicitly provided by a PATCH-style caller."""
    result: dict[str, object] = {}
    for name, value in fields.items():
        if value is _UNSET:
            continue
        if unset_type is not None and isinstance(value, unset_type):
            continue
        if value is None:
            if none_value is _UNSET:
                continue
            value = none_value
        if coerce is not None:
            value = coerce(value)
        result[name] = value
    return result


def apply_present_fields(
    request: Any,
    /,
    *,
    unset_type: type[Any] | tuple[type[Any], ...] | None = None,
    none_value: object = _UNSET,
    coerce: Callable[[object], object] | None = None,
    **fields: object,
) -> None:
    """Set fields that were explicitly provided by a PATCH-style caller.

    Unlike ``apply_optional_fields``, this helper can distinguish an external
    "absent" sentinel from ``None``. That covers public payloads such as
    ``msgspec.UNSET`` where ``None`` means "clear this field".
    """
    for name, value in present_fields(
        unset_type=unset_type,
        none_value=none_value,
        coerce=coerce,
        **fields,
    ).items():
        setattr(request, name, value)


def apply_optional_repeated(
    request: Any,
    field: str,
    values: Iterable[object] | None,
    *,
    wrapper_type: Any | None = None,
    wrapper_field: str = "items",
) -> None:
    """Replace a repeated/wrapper field on a PATCH request when ``values`` is not None.

    ``wrapper_type`` is the proto wrapper message used to distinguish
    "clear" (empty list) from "no change" (field absent). When provided,
    we build the wrapper and ``CopyFrom`` it onto ``request.<field>``.
    Otherwise we assign the list directly via ``request.<field>[:] = ...``.
    """
    if values is None:
        return
    if wrapper_type is not None:
        wrapper = wrapper_type()
        getattr(wrapper, wrapper_field).extend(list(values))
        getattr(request, field).CopyFrom(wrapper)
        return
    target = getattr(request, field)
    del target[:]
    target.extend(list(values))


__all__ = [
    "apply_optional_fields",
    "apply_optional_repeated",
    "apply_present_fields",
    "grpc_call",
    "optional_bool",
    "optional_dt",
    "optional_float",
    "optional_int",
    "optional_str",
    "optional_str_from_int",
    "present_fields",
    "translate_grpc_error",
]
