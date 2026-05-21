from __future__ import annotations

import builtins
from pathlib import Path

import pytest

from service_toolkit.grpc.codegen import _run_protoc
from service_toolkit.grpc.client_codegen import (
    _Field,
    _Method,
    _Service,
    _parse_args,
    _resolve_targets,
    _target_from_env,
    _targets_from_config,
    _render_service_module,
)


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


def test_client_codegen_reads_pyproject_targets(tmp_path: Path) -> None:
    config = tmp_path / "pyproject.toml"
    config.write_text(
        """
[tool.service_toolkit.grpc_client_codegen]

[[tool.service_toolkit.grpc_client_codegen.targets]]
proto_package = "example.generated.v1"
out_dir = "src/example_service/clients/_generated"
channel_key_prefix = "example.service"
target_setting = "settings.server.grpc_target"
timeout_setting = "settings.server.grpc_timeout_seconds"
token_setting = "settings.server.api_token"
settings_import = "example_service.core.config"
resource = "example-resource"
services = ["ExampleService"]
""",
        encoding="utf-8",
    )

    (target,) = _targets_from_config(config)

    assert target.proto_package == "example.generated.v1"
    assert target.out_dir == tmp_path / "src/example_service/clients/_generated"
    assert target.channel_key_prefix == "example.service"
    assert target.token_setting == "settings.server.api_token"
    assert target.settings_import == "example_service.core.config"
    assert target.resource == "example-resource"
    assert target.services == ("ExampleService",)


def test_client_codegen_reads_single_target_from_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LP_GRPC_CLIENTGEN_PROTO_PACKAGE", "env.generated.v1")
    monkeypatch.setenv("LP_GRPC_CLIENTGEN_OUT_DIR", "generated")
    monkeypatch.setenv("LP_GRPC_CLIENTGEN_CHANNEL_KEY_PREFIX", "env.service")
    monkeypatch.setenv("LP_GRPC_CLIENTGEN_TARGET_SETTING", "settings.env.target")
    monkeypatch.setenv(
        "LP_GRPC_CLIENTGEN_TIMEOUT_SETTING",
        "settings.env.timeout_seconds",
    )
    monkeypatch.setenv("LP_GRPC_CLIENTGEN_SERVICES", "OneService, TwoService")

    target = _target_from_env()

    assert target is not None
    assert target.proto_package == "env.generated.v1"
    assert target.out_dir == tmp_path / "generated"
    assert target.channel_key_prefix == "env.service"
    assert target.target_setting == "settings.env.target"
    assert target.timeout_setting == "settings.env.timeout_seconds"
    assert target.token_setting == "settings.internal.token"
    assert target.services == ("OneService", "TwoService")


def test_client_codegen_uses_pyproject_when_no_target_args(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[tool.service_toolkit.grpc_client_codegen]

[[tool.service_toolkit.grpc_client_codegen.targets]]
proto_package = "noargs.generated.v1"
out_dir = "generated"
channel_key_prefix = "noargs.service"
target_setting = "settings.target"
timeout_setting = "settings.timeout_seconds"
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["lp-gen-client"])

    (target,) = _resolve_targets(_parse_args())

    assert target.proto_package == "noargs.generated.v1"
    assert target.out_dir == tmp_path / "generated"
    assert target.channel_key_prefix == "noargs.service"
