"""Shared protobuf/gRPC code generation helper for LeavePulse services.

Generates ``*_pb2.py`` / ``*_pb2_grpc.py`` / ``*_pb2.pyi`` files from a tree
of ``.proto`` definitions. Each target rewrites the generated ``from
leavepulse.*`` imports to live under a consumer-chosen import prefix so
the descriptors load cleanly inside the producer's package namespace.

Two invocation styles are supported:

* CLI args::

      lp-generate-grpc \\
          --proto-dir src/<service>_grpc/proto \\
          --out-dir   src/<service>_grpc/generated \\
          --import-prefix <service>_grpc.generated.leavepulse

* ``pyproject.toml`` (preferred for the canonical service tree, so the
  same config is read by humans, CI and IDE)::

      [[tool.service_toolkit.grpc_proto_codegen.targets]]
      proto_dir = "src/verification_service_grpc/proto"
      out_dir = "src/verification_service_grpc/generated"
      import_prefix = "verification_service_grpc.generated.leavepulse"

  Run with no args::

      uv run lp-generate-grpc

A target may also generate from contracts that live in their own
repositories, so that no consumer keeps a copy of one. It names them, and they
are pinned in the same manifest, in the table ``lp-sync-contract``
(``service_toolkit.grpc.contracts``) materialises::

      [[tool.service_toolkit.grpc_proto_codegen.targets]]
      proto_dir = "src/control_service_grpc/proto"
      out_dir = "src/control_service_grpc/generated"
      import_prefix = "control_service_grpc.generated.leavepulse"
      contracts = ["agent-contract"]

      [tool.service_toolkit.contracts.agent-contract]
      repository = "https://github.com/LeavePulse/agent-contract.git"
      tag = "v1.0.0"
      commit = "<the full commit the tag names>"
      proto_dir = "proto"

Generation only reads the materialised contract and fails, naming the command
to run, when it is not on disk. Its protos are generated into the same output
as the target's own.

A contract that has its own generated distribution is imported instead, never
emitted (``service_toolkit.grpc.imported``). The target names the installed
package; its protos go on the include path only and every generated import of
its modules points at the one copy that distribution ships::

      [[tool.service_toolkit.grpc_proto_codegen.targets]]
      proto_dir = "src/network_service_grpc/proto"
      out_dir = "src/network_service_grpc/generated"
      import_prefix = "network_service_grpc.generated.leavepulse"
      imports = ["agent_contract_grpc"]
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import tomllib
from dataclasses import dataclass
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from service_toolkit.grpc import imported
from service_toolkit.grpc.contracts import ContractPin, proto_root, read_pins


@dataclass(frozen=True, slots=True)
class _ProtoGenerationTarget:
    proto_dir: Path
    out_dir: Path
    import_prefix: str
    #: Further proto roots generated into the same output: the pinned
    #: contracts this target's own protos import from.
    extra_proto_dirs: tuple[Path, ...] = ()
    #: Installed generated contracts: on the include path, never emitted.
    imports: tuple[str, ...] = ()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate Python gRPC stubs for a provider-owned SDK package.",
    )
    parser.add_argument("--proto-dir", type=Path)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--import-prefix")
    parser.add_argument(
        "--config",
        type=Path,
        help="Optional path to a pyproject.toml (or compatible TOML) "
        "containing [[tool.service_toolkit.grpc_proto_codegen.targets]] "
        "entries. Defaults to ./pyproject.toml when no CLI args are given.",
    )
    return parser.parse_args()


def _clean_output(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for path in out_dir.rglob("__pycache__"):
        if path.is_dir():
            shutil.rmtree(path)
    for path in out_dir.rglob("*.py"):
        if path.name != "__init__.py":
            path.unlink()
    for path in out_dir.rglob("*.pyi"):
        path.unlink()


def _collect_proto_files(proto_dir: Path) -> list[str]:
    proto_files = sorted(str(path) for path in proto_dir.rglob("*.proto"))
    if not proto_files:
        msg = f"No .proto files found in {proto_dir}"
        raise SystemExit(msg)
    return proto_files


def _run_protoc(
    *,
    proto_dir: Path,
    out_dir: Path,
    proto_files: list[str],
    extra_proto_dirs: tuple[Path, ...] = (),
) -> None:
    try:
        from grpc_tools import protoc
    except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
        msg = (
            "grpc_tools is not installed. Install service-toolkit with the "
            "'grpc-codegen' extra before running lp-generate-grpc."
        )
        raise SystemExit(msg) from exc

    args = [
        "grpc_tools.protoc",
        f"--proto_path={proto_dir}",
        *(f"--proto_path={extra}" for extra in extra_proto_dirs),
        f"--python_out={out_dir}",
        f"--grpc_python_out={out_dir}",
        f"--pyi_out={out_dir}",
        *proto_files,
    ]
    code = protoc.main(args)
    if code:
        raise SystemExit(code)


_GENERATED_IMPORT = re.compile(
    r"^from (?P<package>leavepulse(?:\.\w+)*) import (?P<module>\w+)",
    re.MULTILINE,
)


def _rewrite_imports(
    out_dir: Path,
    import_prefix: str,
    imported_roots: Mapping[str, str] | None = None,
) -> None:
    """Point each generated ``from leavepulse.… import …`` at its owner.

    A module an imported contract defines resolves under that contract's
    root; everything else is the target's own and moves under its prefix.
    """
    own = import_prefix.rstrip(".")
    roots = imported_roots or {}

    def owner(match: re.Match[str]) -> str:
        package, module = match["package"], match["module"]
        root = roots.get(f"{package}.{module}")
        if root is not None:
            return f"from {root}.{package} import {module}"
        return f"from {own}{package.removeprefix('leavepulse')} import {module}"

    for path in out_dir.rglob("*_pb2*.py"):
        text = path.read_text()
        path.write_text(_GENERATED_IMPORT.sub(owner, text))


def _ensure_package_inits(out_dir: Path) -> None:
    for path in [out_dir, *[p for p in out_dir.rglob("*") if p.is_dir()]]:
        (path / "__init__.py").touch(exist_ok=True)


def _read_key(raw: dict[str, Any], name: str, default: object = None) -> object:
    if name in raw and raw[name] is not None:
        return raw[name]
    return default


def _target_from_mapping(
    raw: dict[str, Any],
    *,
    base_dir: Path,
    contracts: dict[str, ContractPin] | None = None,
) -> _ProtoGenerationTarget:
    missing: list[str] = []

    def required(name: str) -> str:
        value = _read_key(raw, name)
        if value is None or not str(value).strip():
            missing.append(name)
            return ""
        return str(value).strip()

    proto_dir_value = required("proto_dir")
    out_dir_value = required("out_dir")
    import_prefix = required("import_prefix")

    if missing:
        msg = f"Missing gRPC proto generation config fields: {', '.join(missing)}"
        raise SystemExit(msg)

    proto_dir = Path(proto_dir_value)
    if not proto_dir.is_absolute():
        proto_dir = base_dir / proto_dir

    out_dir = Path(out_dir_value)
    if not out_dir.is_absolute():
        out_dir = base_dir / out_dir

    names = raw.get("contracts") or []
    if not isinstance(names, list):
        msg = "a target's contracts must be a list of contract names."
        raise SystemExit(msg)
    known = contracts or {}
    unknown = [str(name) for name in names if str(name) not in known]
    if unknown:
        msg = f"target {out_dir_value} names undeclared contracts: {', '.join(unknown)}"
        raise SystemExit(msg)

    packages = raw.get("imports") or []
    if not isinstance(packages, list) or not all(
        isinstance(package, str) and package.isidentifier() for package in packages
    ):
        msg = "a target's imports must be a list of installed Python package names."
        raise SystemExit(msg)

    return _ProtoGenerationTarget(
        proto_dir=proto_dir,
        out_dir=out_dir,
        import_prefix=import_prefix,
        extra_proto_dirs=tuple(
            proto_root(known[str(name)], base_dir=base_dir) for name in names
        ),
        imports=tuple(packages),
    )


def _get_config_section(data: dict[str, Any]) -> dict[str, Any] | None:
    section = data.get("tool", {}).get("service_toolkit", {}).get("grpc_proto_codegen")
    if not isinstance(section, dict):
        return None
    return section


def _targets_from_config(path: Path) -> list[_ProtoGenerationTarget]:
    path = path.resolve()
    if not path.exists():
        msg = f"gRPC proto generation config not found: {path}"
        raise SystemExit(msg)

    data = tomllib.loads(path.read_text(encoding="utf-8"))
    section = _get_config_section(data)
    if section is None:
        msg = (
            "gRPC proto generation config section not found. "
            "Use [tool.service_toolkit.grpc_proto_codegen]."
        )
        raise SystemExit(msg)

    raw_targets = section.get("targets")
    if not isinstance(raw_targets, list) or not raw_targets:
        msg = "gRPC proto generation config must contain at least one target."
        raise SystemExit(msg)

    base_dir = path.parent
    contracts = read_pins(path)
    return [
        _target_from_mapping(raw, base_dir=base_dir, contracts=contracts)
        for raw in raw_targets
        if isinstance(raw, dict)
    ]


def _target_from_args(args: argparse.Namespace) -> _ProtoGenerationTarget | None:
    if args.proto_dir is None and args.out_dir is None and args.import_prefix is None:
        return None
    missing: list[str] = []
    if args.proto_dir is None:
        missing.append("--proto-dir")
    if args.out_dir is None:
        missing.append("--out-dir")
    if args.import_prefix is None:
        missing.append("--import-prefix")
    if missing:
        msg = (
            f"CLI proto generation target is incomplete; missing: {', '.join(missing)}"
        )
        raise SystemExit(msg)
    return _ProtoGenerationTarget(
        proto_dir=Path(args.proto_dir),
        out_dir=Path(args.out_dir),
        import_prefix=args.import_prefix,
    )


def _default_config_path(args: argparse.Namespace) -> Path | None:
    if args.config is not None:
        return args.config

    env_config = os.environ.get("LP_GRPC_PROTOGEN_CONFIG")
    if env_config:
        return Path(env_config)

    pyproject = Path("pyproject.toml")
    if pyproject.exists():
        return pyproject
    return None


def _resolve_targets(args: argparse.Namespace) -> list[_ProtoGenerationTarget]:
    arg_target = _target_from_args(args)
    if arg_target is not None:
        return [arg_target]

    config_path = _default_config_path(args)
    if config_path is None:
        msg = (
            "No gRPC proto generation target provided. Pass CLI args, set "
            "LP_GRPC_PROTOGEN_CONFIG, or add "
            "[[tool.service_toolkit.grpc_proto_codegen.targets]] to pyproject.toml."
        )
        raise SystemExit(msg)
    return _targets_from_config(config_path)


def _generate_target(target: _ProtoGenerationTarget) -> None:
    proto_dir = target.proto_dir.resolve()
    out_dir = target.out_dir.resolve()

    extra_proto_dirs = tuple(extra.resolve() for extra in target.extra_proto_dirs)
    contracts = [imported.resolve(package) for package in target.imports]

    _clean_output(out_dir)
    proto_files = _collect_proto_files(proto_dir)
    for extra in extra_proto_dirs:
        proto_files.extend(_collect_proto_files(extra))
    imported.check_not_redefined(
        (
            PurePosixPath(Path(file).relative_to(root).as_posix())
            for root in (proto_dir, *extra_proto_dirs)
            for file in proto_files
            if Path(file).is_relative_to(root)
        ),
        contracts,
    )
    _run_protoc(
        proto_dir=proto_dir,
        out_dir=out_dir,
        proto_files=proto_files,
        # Imported contracts resolve imports only; none of their files is in
        # proto_files, so protoc emits nothing for them.
        extra_proto_dirs=(
            *extra_proto_dirs,
            *(contract.proto_root for contract in contracts),
        ),
    )
    _rewrite_imports(out_dir, target.import_prefix, imported.import_roots(contracts))
    _ensure_package_inits(out_dir)
    print(f"generated {target.import_prefix} → {out_dir}")  # noqa: archlint=print


def main() -> None:
    args = _parse_args()
    for target in _resolve_targets(args):
        _generate_target(target)


if __name__ == "__main__":
    main()
