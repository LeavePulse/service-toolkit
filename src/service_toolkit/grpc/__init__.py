"""gRPC infrastructure for LeavePulse inter-service communication."""

from __future__ import annotations

import importlib
import sys

__all__ = [
    "GrpcClientMetricsInterceptor",
    "GrpcServerMetricsInterceptor",
    "GrpcClient",
    "InternalTokenCallCredentials",
    "InternalTokenClientInterceptor",
    "InternalTokenInterceptor",
    "JwtContextServerInterceptor",
    "JwtForwardingClientInterceptor",
    "abort_invalid",
    "abort_not_found",
    "apply_optional_fields",
    "apply_optional_repeated",
    "apply_present_fields",
    "build_grpc_client",
    "build_grpc_lifecycle",
    "build_shared_channel",
    "close_shared_channels",
    "create_grpc_server",
    "current_jwt_payload",
    "user_id_from_payload",
    "db_session",
    "forwarded_jwt",
    "grpc_call",
    "grpc_error_handler",
    "message_has_field",
    "optional_bool",
    "optional_dt",
    "optional_float",
    "optional_int",
    "optional_str",
    "optional_str_from_int",
    "optional_str_if_present",
    "present_fields",
    "reset_forwarded_jwt",
    "set_forwarded_jwt",
    "start_grpc_server",
    "stop_grpc_server",
    "translate_grpc_error",
]

_EXPORT_MODULES = {
    "GrpcClientMetricsInterceptor": ".metrics",
    "GrpcServerMetricsInterceptor": ".metrics",
    "GrpcClient": ".client",
    "InternalTokenCallCredentials": ".interceptors",
    "InternalTokenClientInterceptor": ".interceptors",
    "InternalTokenInterceptor": ".interceptors",
    "JwtContextServerInterceptor": ".jwt_forwarding",
    "JwtForwardingClientInterceptor": ".jwt_forwarding",
    "abort_invalid": ".servicer",
    "abort_not_found": ".servicer",
    "apply_optional_fields": ".calls",
    "apply_optional_repeated": ".calls",
    "apply_present_fields": ".calls",
    "build_grpc_client": ".client",
    "build_grpc_lifecycle": ".server",
    "build_shared_channel": ".channels",
    "close_shared_channels": ".channels",
    "create_grpc_server": ".server",
    "current_jwt_payload": ".jwt_forwarding",
    "user_id_from_payload": ".jwt_forwarding",
    "db_session": ".servicer",
    "forwarded_jwt": ".jwt_forwarding",
    "grpc_call": ".calls",
    "grpc_error_handler": ".servicer",
    "message_has_field": ".calls",
    "optional_bool": ".calls",
    "optional_dt": ".calls",
    "optional_float": ".calls",
    "optional_int": ".calls",
    "optional_str": ".calls",
    "optional_str_from_int": ".calls",
    "optional_str_if_present": ".calls",
    "present_fields": ".calls",
    "reset_forwarded_jwt": ".jwt_forwarding",
    "set_forwarded_jwt": ".jwt_forwarding",
    "start_grpc_server": ".server",
    "stop_grpc_server": ".server",
    "translate_grpc_error": ".calls",
}


def __getattr__(name: str):
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(name)

    try:
        module = importlib.import_module(module_name, __name__)
    except ModuleNotFoundError as exc:
        missing = exc.name or ""
        if missing == "grpc":
            raise ModuleNotFoundError(
                "gRPC helpers require the optional 'grpc' extra. "
                "Install with 'pip install service-toolkit[grpc]'."
            ) from exc
        raise

    value = getattr(module, name)
    setattr(sys.modules[__name__], name, value)
    return value


def __dir__() -> list[str]:
    return sorted(set(__all__))
