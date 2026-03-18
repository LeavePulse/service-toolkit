"""Shared protobuf/gRPC code generation helper for LeavePulse services."""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate Python gRPC stubs for a provider-owned SDK package.",
    )
    parser.add_argument("--proto-dir", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--import-prefix", required=True)
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


def _run_protoc(*, proto_dir: Path, out_dir: Path, proto_files: list[str]) -> None:
    try:
        from grpc_tools import protoc
    except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
        msg = (
            "grpc_tools is not installed. Install service-toolkit with the "
            "'grpc' extra before running lp-generate-grpc."
        )
        raise SystemExit(msg) from exc

    args = [
        "grpc_tools.protoc",
        f"--proto_path={proto_dir}",
        f"--python_out={out_dir}",
        f"--grpc_python_out={out_dir}",
        f"--pyi_out={out_dir}",
        *proto_files,
    ]
    code = protoc.main(args)
    if code:
        raise SystemExit(code)


def _rewrite_imports(out_dir: Path, import_prefix: str) -> None:
    pattern = re.compile(r"^from leavepulse\.", re.MULTILINE)
    replacement = f"from {import_prefix.rstrip('.') }."
    for path in out_dir.rglob("*_pb2*.py"):
        text = path.read_text()
        path.write_text(pattern.sub(replacement, text))


def _ensure_package_inits(out_dir: Path) -> None:
    for path in [out_dir, *[p for p in out_dir.rglob("*") if p.is_dir()]]:
        (path / "__init__.py").touch(exist_ok=True)


def main() -> None:
    args = _parse_args()
    proto_dir = args.proto_dir.resolve()
    out_dir = args.out_dir.resolve()

    _clean_output(out_dir)
    proto_files = _collect_proto_files(proto_dir)
    _run_protoc(proto_dir=proto_dir, out_dir=out_dir, proto_files=proto_files)
    _rewrite_imports(out_dir, args.import_prefix)
    _ensure_package_inits(out_dir)


if __name__ == "__main__":
    main()
