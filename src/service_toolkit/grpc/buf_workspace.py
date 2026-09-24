"""Assemble a Buf v2 workspace from a service's protos and its pinned contracts.

A service whose protos import a contract kept in another repository cannot run
``buf`` on its own tree: the import resolves nowhere. This builds a workspace in
which the service's own protos and each pinned contract are local modules, so
``buf build``, ``buf lint``, ``buf format`` and ``buf breaking`` run against the
same contract revision codegen uses, with no registry.

The workspace is a build input, not a source. It is assembled from the one
``[[tool.service_toolkit.grpc_proto_codegen.targets]]`` entry in the manifest
and the contracts it names (already materialised by ``lp-sync-contract``), into
``.contracts/buf/<digest>/`` where the digest covers every file placed in it.
The service's module is ``modules/local``; each contract is ``modules/<name>``.

The Buf policy lives in the manifest, with paths relative to the service's own
proto directory, and is written into the workspace with those paths moved under
``modules/local``::

    [tool.service_toolkit.buf.lint]
    use = ["STANDARD"]
    except = ["SERVICE_SUFFIX"]
    ignore_only = { RPC_RESPONSE_STANDARD_NAME = ["leavepulse/fleet/v1/fleet.proto"] }

    [tool.service_toolkit.buf.breaking]
    use = ["FILE"]

Run as ``lp-buf-workspace [--manifest PATH]``; it prints the workspace path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import tomllib
from pathlib import Path, PurePosixPath
from typing import Any

from service_toolkit.grpc.contracts import ContractError, proto_root, read_pins

LOCAL_MODULE = "modules/local"


def _policy(manifest: Path) -> dict[str, Any]:
    data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    policy = data.get("tool", {}).get("service_toolkit", {}).get("buf")
    if not isinstance(policy, dict) or not policy:
        msg = f"{manifest}: no [tool.service_toolkit.buf] policy to check against."
        raise ContractError(msg)
    unknown = set(policy) - {"lint", "breaking"}
    if unknown:
        msg = f"{manifest}: [tool.service_toolkit.buf] has unknown keys {sorted(unknown)}."
        raise ContractError(msg)
    return {
        key: _moved_under_local(manifest, key, value) for key, value in policy.items()
    }


def _local_path(manifest: Path, path: str) -> str:
    relative = PurePosixPath(path)
    if relative.is_absolute() or ".." in relative.parts:
        msg = f"{manifest}: buf policy path {path!r} is not inside the proto directory."
        raise ContractError(msg)
    return str(PurePosixPath(LOCAL_MODULE) / relative)


def _moved_under_local(manifest: Path, section: str, value: object) -> object:
    """Rewrite the policy's file paths from the proto root onto the module."""
    if not isinstance(value, dict):
        msg = f"{manifest}: [tool.service_toolkit.buf.{section}] must be a table."
        raise ContractError(msg)
    moved = dict(value)
    if "ignore" in moved:
        moved["ignore"] = [_local_path(manifest, p) for p in moved["ignore"]]
    if "ignore_only" in moved:
        moved["ignore_only"] = {
            rule: [_local_path(manifest, p) for p in paths]
            for rule, paths in moved["ignore_only"].items()
        }
    return moved


def _single_target(manifest: Path) -> tuple[Path, list[str]]:
    """The target's own proto directory, inside the manifest root, and its contracts."""
    data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    targets = (
        data.get("tool", {})
        .get("service_toolkit", {})
        .get("grpc_proto_codegen", {})
        .get("targets", [])
    )
    if not isinstance(targets, list) or len(targets) != 1:
        msg = (
            f"{manifest}: a Buf workspace is assembled from exactly one "
            "grpc_proto_codegen target."
        )
        raise ContractError(msg)
    [target] = targets
    root = manifest.parent.resolve()
    proto_dir = (root / str(target.get("proto_dir", ""))).resolve()
    if (
        not proto_dir.is_relative_to(root)
        or proto_dir == root
        or not proto_dir.is_dir()
    ):
        msg = f"{manifest}: proto_dir {target.get('proto_dir')!r} is not a directory inside {root}."
        raise ContractError(msg)
    return proto_dir, [str(name) for name in target.get("contracts") or []]


def _files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.proto") if p.is_file())


def build_workspace(manifest: Path) -> Path:
    """Assemble the workspace for *manifest* and return its directory."""
    manifest = manifest.resolve()
    base_dir = manifest.parent
    proto_dir, names = _single_target(manifest)
    pins = read_pins(manifest)
    unknown = [name for name in names if name not in pins]
    if unknown:
        msg = f"{manifest}: the target names unpinned contracts {unknown}."
        raise ContractError(msg)

    modules = {LOCAL_MODULE: proto_dir}
    for name in names:
        modules[f"modules/{name}"] = proto_root(pins[name], base_dir=base_dir)

    config = {
        "version": "v2",
        "modules": [{"path": module} for module in modules],
        **_policy(manifest),
    }
    buf_yaml = json.dumps(config, indent=2, sort_keys=True) + "\n"

    digest = hashlib.sha256(buf_yaml.encode())
    placed: list[tuple[str, Path]] = []
    for module, source in modules.items():
        for file in _files(source):
            relative = f"{module}/{file.relative_to(source).as_posix()}"
            digest.update(relative.encode() + b"\0" + file.read_bytes() + b"\0")
            placed.append((relative, file))

    buf_root = base_dir / ".contracts" / "buf"
    destination = buf_root / digest.hexdigest()
    buf_root.mkdir(parents=True, exist_ok=True)
    # Assembled afresh every time and swapped in whole: a workspace directory is
    # never trusted for existing, only for having just been built from inputs.
    with tempfile.TemporaryDirectory(dir=buf_root) as scratch:
        staging = Path(scratch) / "workspace"
        for relative, file in placed:
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(file, target)
        (staging / "buf.yaml").write_text(buf_yaml)
        if destination.exists():
            shutil.rmtree(destination)
        staging.rename(destination)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Assemble a Buf workspace from a service's protos and its pinned contracts.",
    )
    parser.add_argument("--manifest", type=Path, default=Path("pyproject.toml"))
    args = parser.parse_args()
    print(build_workspace(args.manifest))  # noqa: archlint=print


if __name__ == "__main__":
    main()
