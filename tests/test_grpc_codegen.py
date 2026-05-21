from __future__ import annotations

import builtins
from pathlib import Path

import pytest

from service_toolkit.grpc.codegen import _run_protoc
from service_toolkit.grpc.client_codegen import _Field, _Method, _Service, _render_service_module


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


def test_client_codegen_respects_settings_import() -> None:
    source = _render_service_module(
        service=_Service(
            proto_module="example_pb2",
            grpc_module="example_pb2_grpc",
            service_name="ExampleService",
            stub_class="ExampleServiceStub",
            methods=(
                _Method(
                    rpc_name="DoThing",
                    snake_name="do_thing",
                    input_type="example_pb2.DoThingRequest",
                    output_type="example_pb2.DoThingResponse",
                    fields=(
                        _Field(
                            name="server_id",
                            py_type="int",
                            is_repeated=False,
                            is_optional=False,
                            is_message=False,
                        ),
                    ),
                ),
            ),
        ),
        proto_package="example.generated.v1",
        channel_key="example.service",
        target_setting="settings.server.grpc_target",
        timeout_setting="settings.server.grpc_timeout_seconds",
        token_setting="settings.server.api_token",
        settings_import="example_service.core.config",
        resource="example",
    )

    assert "from example_service.core.config import settings" in source
    assert "target=settings.server.grpc_target" in source
    assert "timeout_seconds=settings.server.grpc_timeout_seconds" in source
