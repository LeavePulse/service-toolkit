"""Generate low-level Python gRPC client wrappers from compiled proto descriptors.

Reads ``*_pb2.py`` modules that already exist in the import path (the
producer service's published gRPC package) and emits one ``<service>_grpc.py``
per proto service containing:

  - shared channel built via ``service_toolkit.grpc.build_shared_channel``
  - one ``async def`` per RPC method, accepting kwargs that mirror the
    request message fields, returning the raw response proto

The generated file is meant to be checked into the consumer repo. A
hand-written client lives next to it and does the public-model wrapping,
tri-state translation, custom resource_id, etc.

Usage::

    lp-gen-client \
        --proto-package whitelist_service_grpc.generated.leavepulse.whitelist.v1 \
        --out-file platform-api/src/platform_api/clients/_generated/whitelist_grpc.py \
        --channel-key platform_api.whitelist \
        --target-setting "settings.whitelist.target" \
        --timeout-setting "settings.whitelist.timeout_seconds" \
        --token-setting "settings.internal.token"
"""

from __future__ import annotations

import argparse
import importlib
import pkgutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Field type IDs from google.protobuf.descriptor.FieldDescriptor.
# We only need a handful — anything more exotic falls back to ``Any``.
_TYPE_STRING = 9
_TYPE_BOOL = 8
_TYPE_BYTES = 12
_TYPE_FLOAT = 2
_TYPE_DOUBLE = 1
_TYPE_INT32 = 5
_TYPE_INT64 = 3
_TYPE_UINT32 = 13
_TYPE_UINT64 = 4
_TYPE_SINT32 = 17
_TYPE_SINT64 = 18
_TYPE_FIXED32 = 7
_TYPE_FIXED64 = 6
_TYPE_SFIXED32 = 15
_TYPE_SFIXED64 = 16
_TYPE_ENUM = 14
_TYPE_MESSAGE = 11

_INT_TYPES = frozenset(
    {
        _TYPE_INT32,
        _TYPE_INT64,
        _TYPE_UINT32,
        _TYPE_UINT64,
        _TYPE_SINT32,
        _TYPE_SINT64,
        _TYPE_FIXED32,
        _TYPE_FIXED64,
        _TYPE_SFIXED32,
        _TYPE_SFIXED64,
        _TYPE_ENUM,
    }
)
_FLOAT_TYPES = frozenset({_TYPE_FLOAT, _TYPE_DOUBLE})


@dataclass(frozen=True, slots=True)
class _Field:
    name: str
    py_type: str
    is_repeated: bool
    is_optional: bool
    is_message: bool


@dataclass(frozen=True, slots=True)
class _Method:
    rpc_name: str
    snake_name: str
    input_type: str  # e.g. ``voting_pb2.GetVotingLinksRequest``
    output_type: str
    fields: tuple[_Field, ...]


@dataclass(frozen=True, slots=True)
class _Service:
    proto_module: str  # e.g. ``voting_pb2``
    grpc_module: str  # e.g. ``voting_pb2_grpc``
    service_name: str  # e.g. ``VotingService``
    stub_class: str  # e.g. ``VotingServiceStub``
    methods: tuple[_Method, ...]


def _snake(name: str) -> str:
    out: list[str] = []
    for i, ch in enumerate(name):
        if ch.isupper() and i > 0 and (
            (not name[i - 1].isupper()) or (i + 1 < len(name) and name[i + 1].islower())
        ):
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


def _py_type_for(field: Any) -> str:
    """Map a FieldDescriptor type to a Python annotation string."""
    if field.type == _TYPE_STRING:
        return "str"
    if field.type == _TYPE_BOOL:
        return "bool"
    if field.type == _TYPE_BYTES:
        return "bytes"
    if field.type in _INT_TYPES:
        return "int"
    if field.type in _FLOAT_TYPES:
        return "float"
    if field.type == _TYPE_MESSAGE:
        return "Any"
    return "Any"


def _collect_fields(descriptor: Any) -> tuple[_Field, ...]:
    fields: list[_Field] = []
    for f in descriptor.fields:
        py = _py_type_for(f)
        # ``label == LABEL_REPEATED`` (3) means repeated/map field.
        is_repeated = f.label == 3
        # proto3 ``optional`` keyword carries a synthetic oneof.
        is_optional = bool(f.containing_oneof and f.containing_oneof.name.startswith("_"))
        is_message = f.type == _TYPE_MESSAGE
        fields.append(
            _Field(
                name=f.name,
                py_type=py,
                is_repeated=is_repeated,
                is_optional=is_optional,
                is_message=is_message,
            )
        )
    return tuple(fields)


def _scan_package(proto_package: str) -> list[_Service]:
    """Walk every ``*_pb2_grpc`` in the package and extract service info."""
    pkg = importlib.import_module(proto_package)
    if not hasattr(pkg, "__path__"):
        msg = f"{proto_package!r} is not a package"
        raise SystemExit(msg)

    services: list[_Service] = []
    for module_info in pkgutil.iter_modules(pkg.__path__):
        if not module_info.name.endswith("_pb2_grpc"):
            continue
        grpc_module_name = f"{proto_package}.{module_info.name}"
        pb2_module_name = grpc_module_name[: -len("_grpc")]
        grpc_module = importlib.import_module(grpc_module_name)
        pb2_module = importlib.import_module(pb2_module_name)
        file_descriptor = pb2_module.DESCRIPTOR
        for service in file_descriptor.services_by_name.values():
            short_pb2 = pb2_module_name.rsplit(".", 1)[1]
            short_grpc = grpc_module_name.rsplit(".", 1)[1]
            stub_class_name = f"{service.name}Stub"
            if not hasattr(grpc_module, stub_class_name):
                continue
            methods: list[_Method] = []
            for method in service.methods:
                input_descriptor = method.input_type
                fields = _collect_fields(input_descriptor)
                methods.append(
                    _Method(
                        rpc_name=method.name,
                        snake_name=_snake(method.name),
                        input_type=f"{short_pb2}.{input_descriptor.name}",
                        output_type=f"{short_pb2}.{method.output_type.name}",
                        fields=fields,
                    )
                )
            services.append(
                _Service(
                    proto_module=short_pb2,
                    grpc_module=short_grpc,
                    service_name=service.name,
                    stub_class=stub_class_name,
                    methods=tuple(methods),
                )
            )
    return services


def _render_signature(method: _Method) -> str:
    """Generate the kwargs-only signature for one async function.

    Required fields come first (no default), optional/repeated ones come
    last with ``= None`` / ``()``. Repeated fields take ``Iterable[T]``.
    """
    required: list[str] = []
    optional: list[str] = []
    for field in method.fields:
        if field.is_repeated:
            optional.append(f"{field.name}: Iterable[{field.py_type}] = ()")
        elif field.is_optional:
            optional.append(f"{field.name}: {field.py_type} | None = None")
        elif field.is_message:
            optional.append(f"{field.name}: Any | None = None")
        else:
            required.append(f"{field.name}: {field.py_type}")
    grpc_options = [
        "grpc_resource: str | None = None",
        "grpc_resource_id: object = None",
        "grpc_extra_errors: Mapping[grpc.StatusCode, Any] | None = None",
    ]
    parts = ["*"] + required + optional + grpc_options
    return ", ".join(parts)


def _render_request_build(method: _Method) -> str:
    """Generate the request-construction block as a flat newline-joined string.

    The returned text contains plain ``stmt\\nstmt`` lines without leading
    indent. ``_render_method`` later prepends a uniform 4-space indent so
    nested ``if`` blocks (for repeated/message fields) line up correctly.
    """
    plain_kwargs: list[str] = []
    optional_kwargs: list[str] = []
    multi_blocks: list[str] = []
    for field in method.fields:
        if field.is_repeated:
            multi_blocks.append(
                f"if {field.name}:\n    request.{field.name}.extend({field.name})"
            )
        elif field.is_optional:
            optional_kwargs.append(field.name)
        elif field.is_message:
            multi_blocks.append(
                f"if {field.name} is not None:\n    request.{field.name}.CopyFrom({field.name})"
            )
        else:
            plain_kwargs.append(f"{field.name}={field.name}")

    lines: list[str] = []
    if plain_kwargs:
        joined = ", ".join(plain_kwargs)
        lines.append(f"request = {method.input_type}({joined})")
    else:
        lines.append(f"request = {method.input_type}()")
    if optional_kwargs:
        kw = ", ".join(f"{n}={n}" for n in optional_kwargs)
        lines.append(f"apply_optional_fields(request, {kw})")
    lines.extend(multi_blocks)
    return "\n".join(lines)


def _render_method(method: _Method, *, resource: str) -> str:
    sig = _render_signature(method)
    body_lines = _render_request_build(method).split("\n")
    indented_body = "\n".join("    " + line for line in body_lines)
    return (
        f"async def {method.snake_name}({sig}) -> {method.output_type}:\n"
        f"{indented_body}\n"
        f"    return await _CLIENT.call(\n"
        f'        _STUBS["{method.rpc_name}"],\n'
        f"        request,\n"
        f"        resource=grpc_resource if grpc_resource is not None else {resource!r},\n"
        f"        resource_id=grpc_resource_id,\n"
        f"        extra_errors=grpc_extra_errors,\n"
        f"    )\n"
    )


def _render_service_module(
    *,
    service: _Service,
    proto_package: str,
    channel_key: str,
    target_setting: str,
    timeout_setting: str,
    token_setting: str,
    resource: str,
) -> str:
    methods_src = "\n\n".join(_render_method(m, resource=resource) for m in service.methods)
    stub_lookups = ",\n    ".join(
        f'"{m.rpc_name}": _STUB.{m.rpc_name}' for m in service.methods
    )

    header_lines = [
        f'"""AUTOGENERATED gRPC client for {service.service_name}.',
        "",
        "Do not edit by hand — regenerate via ``lp-gen-client``.",
        f"Source proto package: ``{proto_package}``",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "from collections.abc import Iterable, Mapping  # noqa: F401",
        "from typing import Any  # noqa: F401",
        "",
        "import grpc  # noqa: F401",
        "",
        f"from {proto_package} import {service.proto_module}, {service.grpc_module}",
        "from service_toolkit.grpc import apply_optional_fields, build_grpc_client",
        "",
        "from platform_api.core.config import settings",
        "",
        f"_CHANNEL_KEY = {channel_key!r}",
        "_CLIENT = build_grpc_client(",
        "    key=_CHANNEL_KEY,",
        f"    target={target_setting},",
        f"    token={token_setting},",
        f"    timeout_seconds={timeout_setting},",
        ")",
        f"_STUB = _CLIENT.stub({service.grpc_module}.{service.stub_class})",
        "_STUBS = {",
        f"    {stub_lookups},",
        "}",
        "",
        "",
        "async def close() -> None:",
        "    await _CLIENT.close()",
        "",
        "",
    ]
    return "\n".join(header_lines) + methods_src + "\n"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate platform-api low-level gRPC clients from proto descriptors.",
    )
    parser.add_argument(
        "--proto-package",
        required=True,
        help="Importable Python package containing ``*_pb2_grpc`` modules.",
    )
    parser.add_argument(
        "--out-dir",
        required=True,
        type=Path,
        help="Directory to write ``<service>_grpc.py`` files into.",
    )
    parser.add_argument(
        "--channel-key-prefix",
        required=True,
        help='Prefix for the channel key, e.g. "platform_api.whitelist".',
    )
    parser.add_argument(
        "--target-setting",
        required=True,
        help='Python expression yielding the gRPC target, e.g. "settings.whitelist.target".',
    )
    parser.add_argument(
        "--timeout-setting",
        required=True,
        help='Python expression yielding the timeout, e.g. "settings.whitelist.timeout_seconds".',
    )
    parser.add_argument(
        "--token-setting",
        default="settings.internal.token",
        help='Python expression yielding the internal token (default: settings.internal.token).',
    )
    parser.add_argument(
        "--resource",
        default="resource",
        help='Default ``resource`` label for grpc_call error translation.',
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    services = _scan_package(args.proto_package)
    if not services:
        msg = f"No gRPC services found in {args.proto_package!r}"
        raise SystemExit(msg)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for service in services:
        source = _render_service_module(
            service=service,
            proto_package=args.proto_package,
            # All RPCs to the same upstream share one channel — only stubs differ.
            channel_key=args.channel_key_prefix,
            target_setting=args.target_setting,
            timeout_setting=args.timeout_setting,
            token_setting=args.token_setting,
            resource=args.resource,
        )
        out_file = args.out_dir / f"{_snake(service.service_name)}_grpc.py"
        out_file.write_text(source)
        print(f"wrote {out_file}")


if __name__ == "__main__":
    main()
