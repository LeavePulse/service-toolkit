from __future__ import annotations

import builtins
from pathlib import Path

import pytest

from service_toolkit.grpc.codegen import _run_protoc


def test_run_protoc_reports_grpc_codegen_extra(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    original_import = builtins.__import__

    def _fake_import(
        name: str,
        globals: dict[str, object] | None = None,
        locals: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if name == "grpc_tools":
            raise ModuleNotFoundError("No module named 'grpc_tools'")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    with pytest.raises(SystemExit, match="grpc-codegen"):
        _run_protoc(
            proto_dir=tmp_path,
            out_dir=tmp_path,
            proto_files=["leavepulse/common/v1/common.proto"],
        )
