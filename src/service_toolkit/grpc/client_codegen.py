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
        --out-dir platform-api/src/platform_api/clients/_generated/whitelist \
        --channel-key-prefix platform_api.whitelist \
        --target-setting "settings.whitelist.target" \
        --timeout-setting "settings.whitelist.timeout_seconds" \
        --token-setting "settings.internal.token" \
        --settings-import platform_api.core.config
"""

from __future__ import annotations

import argparse
import importlib
import os
import pkgutil
import tomllib
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


@dataclass(frozen=True, slots=True)
class _GenerationTarget:
    proto_package: str
    out_dir: Path
    channel_key_prefix: str
    target_setting: str
    timeout_setting: str
    token_setting: str
    settings_import: str
    resource: str
    services: tuple[str, ...]


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


def _scan_package(
    proto_package: str,
    *,
    service_names: set[str] | None = None,
) -> list[_Service]:
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
            if service_names is not None and service.name not in service_names:
                continue
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
        elif field.is_message:
            optional.append(f"{field.name}: Any | None = None")
        elif field.is_optional:
            optional.append(f"{field.name}: {field.py_type} | None = None")
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
        elif field.is_message:
            multi_blocks.append(
                f"if {field.name} is not None:\n    request.{field.name}.CopyFrom({field.name})"
            )
        elif field.is_optional:
            optional_kwargs.append(field.name)
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
    settings_import: str,
    resource: str,
) -> str:
    methods_src = "\n\n".join(_render_method(m, resource=resource) for m in service.methods)
    stub_lookups = ",\n    ".join(
        f'"{m.rpc_name}": _STUB.{m.rpc_name}' for m in service.methods
    )
    uses_repeated = any(
        field.is_repeated for method in service.methods for field in method.fields
    )
    uses_optional_fields = any(
        field.is_optional for method in service.methods for field in method.fields
    )
    collections_import = ["Mapping"]
    if uses_repeated:
        collections_import.insert(0, "Iterable")

    toolkit_imports = ["build_grpc_client"]
    if uses_optional_fields:
        toolkit_imports.insert(0, "apply_optional_fields")

    def import_lines(module: str, names: list[str]) -> list[str]:
        if len(names) == 1:
            return [f"from {module} import {names[0]}"]
        return [
            f"from {module} import (",
            *(f"    {name}," for name in names),
            ")",
        ]

    proto_import_lines = [
        f"from {proto_package} import (",
        f"    {service.proto_module},",
        f"    {service.grpc_module},",
        ")",
    ]
    toolkit_import_lines = import_lines("service_toolkit.grpc", toolkit_imports)
    service_toolkit_module = "service_toolkit.grpc"
    if proto_package < service_toolkit_module:
        grpc_import_lines = [*proto_import_lines, *toolkit_import_lines]
    else:
        grpc_import_lines = [*toolkit_import_lines, *proto_import_lines]

    header_lines = [
        f'"""AUTOGENERATED gRPC client for {service.service_name}.',
        "",
        "Do not edit by hand — regenerate via ``lp-gen-client``.",
        f"Source proto package: ``{proto_package}``",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        *import_lines("collections.abc", collections_import),
        "from typing import Any",
        "",
        "import grpc",
        *grpc_import_lines,
        "",
        f"from {settings_import} import settings",
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
        "",
    ]
    return ("\n".join(header_lines) + methods_src).rstrip() + "\n"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate low-level gRPC clients from proto descriptors.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help=(
            "TOML config path. Defaults to LP_GRPC_CLIENTGEN_CONFIG or "
            "./pyproject.toml when no explicit target args are provided."
        ),
    )
    parser.add_argument(
        "--proto-package",
        help="Importable Python package containing ``*_pb2_grpc`` modules.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        help="Directory to write ``<service>_grpc.py`` files into.",
    )
    parser.add_argument(
        "--channel-key-prefix",
        help='Prefix for the channel key, e.g. "platform_api.whitelist".',
    )
    parser.add_argument(
        "--target-setting",
        help='Python expression yielding the gRPC target, e.g. "settings.whitelist.target".',
    )
    parser.add_argument(
        "--timeout-setting",
        help='Python expression yielding the timeout, e.g. "settings.whitelist.timeout_seconds".',
    )
    parser.add_argument(
        "--token-setting",
        help='Python expression yielding the internal token (default: settings.internal.token).',
    )
    parser.add_argument(
        "--settings-import",
        help=(
            "Module path that exports ``settings`` "
            '(default: "platform_api.core.config").'
        ),
    )
    parser.add_argument(
        "--resource",
        help='Default ``resource`` label for grpc_call error translation.',
    )
    parser.add_argument(
        "--service",
        action="append",
        dest="services",
        help="Proto service name to generate. May be passed multiple times.",
    )
    return parser.parse_args()


def _get_config_section(data: dict[str, Any]) -> dict[str, Any] | None:
    tool = data.get("tool")
    if not isinstance(tool, dict):
        return None

    candidates = (
        ("service_toolkit", "grpc_client_codegen"),
        ("service-toolkit", "grpc-client-codegen"),
        ("service_toolkit", "grpc-client-codegen"),
        ("service-toolkit", "grpc_client_codegen"),
    )
    for first, second in candidates:
        section = tool.get(first)
        if isinstance(section, dict) and isinstance(section.get(second), dict):
            return section[second]
    return None


def _read_key(raw: dict[str, Any], name: str, default: object = None) -> object:
    hyphen_name = name.replace("_", "-")
    if name in raw and raw[name] is not None:
        return raw[name]
    if hyphen_name in raw and raw[hyphen_name] is not None:
        return raw[hyphen_name]
    return default


def _coerce_services(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(item.strip() for item in value.split(",") if item.strip())
    if isinstance(value, list):
        return tuple(str(item).strip() for item in value if str(item).strip())
    msg = "services must be a string or list of strings"
    raise SystemExit(msg)


def _target_from_mapping(raw: dict[str, Any], *, base_dir: Path) -> _GenerationTarget:
    missing: list[str] = []

    def required(name: str) -> str:
        value = _read_key(raw, name)
        if value is None or not str(value).strip():
            missing.append(name)
            return ""
        return str(value).strip()

    proto_package = required("proto_package")
    out_dir_value = required("out_dir")
    channel_key_prefix = required("channel_key_prefix")
    target_setting = required("target_setting")
    timeout_setting = required("timeout_setting")

    if missing:
        msg = f"Missing gRPC client generation config fields: {', '.join(missing)}"
        raise SystemExit(msg)

    out_dir = Path(out_dir_value)
    if not out_dir.is_absolute():
        out_dir = base_dir / out_dir

    return _GenerationTarget(
        proto_package=proto_package,
        out_dir=out_dir,
        channel_key_prefix=channel_key_prefix,
        target_setting=target_setting,
        timeout_setting=timeout_setting,
        token_setting=str(_read_key(raw, "token_setting", "settings.internal.token")),
        settings_import=str(
            _read_key(raw, "settings_import", "platform_api.core.config")
        ),
        resource=str(_read_key(raw, "resource", "resource")),
        services=_coerce_services(_read_key(raw, "services")),
    )


def _targets_from_config(path: Path) -> list[_GenerationTarget]:
    path = path.resolve()
    if not path.exists():
        msg = f"gRPC client generation config not found: {path}"
        raise SystemExit(msg)

    data = tomllib.loads(path.read_text(encoding="utf-8"))
    section = _get_config_section(data)
    if section is None:
        msg = (
            "gRPC client generation config section not found. "
            "Use [tool.service_toolkit.grpc_client_codegen]."
        )
        raise SystemExit(msg)

    raw_targets = section.get("targets")
    if not isinstance(raw_targets, list) or not raw_targets:
        msg = "gRPC client generation config must contain at least one target."
        raise SystemExit(msg)

    base_dir = path.parent
    return [
        _target_from_mapping(raw, base_dir=base_dir)
        for raw in raw_targets
        if isinstance(raw, dict)
    ]


def _target_from_env() -> _GenerationTarget | None:
    prefix = "LP_GRPC_CLIENTGEN_"
    raw = {
        "proto_package": os.environ.get(f"{prefix}PROTO_PACKAGE"),
        "out_dir": os.environ.get(f"{prefix}OUT_DIR"),
        "channel_key_prefix": os.environ.get(f"{prefix}CHANNEL_KEY_PREFIX"),
        "target_setting": os.environ.get(f"{prefix}TARGET_SETTING"),
        "timeout_setting": os.environ.get(f"{prefix}TIMEOUT_SETTING"),
        "token_setting": os.environ.get(f"{prefix}TOKEN_SETTING"),
        "settings_import": os.environ.get(f"{prefix}SETTINGS_IMPORT"),
        "resource": os.environ.get(f"{prefix}RESOURCE"),
        "services": os.environ.get(f"{prefix}SERVICES"),
    }
    if raw["proto_package"] is None:
        return None
    return _target_from_mapping(raw, base_dir=Path.cwd())


def _target_from_args(args: argparse.Namespace) -> _GenerationTarget | None:
    raw = {
        "proto_package": args.proto_package,
        "out_dir": args.out_dir,
        "channel_key_prefix": args.channel_key_prefix,
        "target_setting": args.target_setting,
        "timeout_setting": args.timeout_setting,
        "token_setting": args.token_setting,
        "settings_import": args.settings_import,
        "resource": args.resource,
        "services": args.services,
    }
    if all(value is None for value in raw.values()):
        return None
    return _target_from_mapping(raw, base_dir=Path.cwd())


def _default_config_path(args: argparse.Namespace) -> Path | None:
    if args.config is not None:
        return args.config

    env_config = os.environ.get("LP_GRPC_CLIENTGEN_CONFIG")
    if env_config:
        return Path(env_config)

    pyproject = Path("pyproject.toml")
    if pyproject.exists():
        return pyproject
    return None


def _resolve_targets(args: argparse.Namespace) -> list[_GenerationTarget]:
    arg_target = _target_from_args(args)
    if arg_target is not None:
        return [arg_target]

    env_target = _target_from_env()
    if env_target is not None:
        return [env_target]

    config_path = _default_config_path(args)
    if config_path is None:
        msg = (
            "No gRPC client generation target provided. Pass CLI args, set "
            "LP_GRPC_CLIENTGEN_* env vars, or add "
            "[tool.service_toolkit.grpc_client_codegen] to pyproject.toml."
        )
        raise SystemExit(msg)
    return _targets_from_config(config_path)


def _generate_target(target: _GenerationTarget) -> None:
    requested_services = set(target.services or ()) or None
    services = _scan_package(
        target.proto_package,
        service_names=requested_services,
    )
    if not services:
        suffix = ""
        if requested_services is not None:
            suffix = f" matching {sorted(requested_services)!r}"
        msg = f"No gRPC services found in {target.proto_package!r}{suffix}"
        raise SystemExit(msg)

    target.out_dir.mkdir(parents=True, exist_ok=True)
    for service in services:
        source = _render_service_module(
            service=service,
            proto_package=target.proto_package,
            # All RPCs to the same upstream share one channel — only stubs differ.
            channel_key=target.channel_key_prefix,
            target_setting=target.target_setting,
            timeout_setting=target.timeout_setting,
            token_setting=target.token_setting,
            settings_import=target.settings_import,
            resource=target.resource,
        )
        out_file = target.out_dir / f"{_snake(service.service_name)}_grpc.py"
        out_file.write_text(source)
        print(f"wrote {out_file}")


def main() -> None:
    args = _parse_args()
    for target in _resolve_targets(args):
        _generate_target(target)


if __name__ == "__main__":
    main()
