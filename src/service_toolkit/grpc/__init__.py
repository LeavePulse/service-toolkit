"""gRPC infrastructure for LeavePulse inter-service communication."""

from __future__ import annotations

import importlib
import sys

__all__ = [
    "GrpcClientMetricsInterceptor",
    "GrpcServerMetricsInterceptor",
    "InternalTokenCallCredentials",
    "InternalTokenClientInterceptor",
    "InternalTokenInterceptor",
    "JwtContextServerInterceptor",
    "JwtForwardingClientInterceptor",
    "abort_invalid",
    "abort_not_found",
    "build_grpc_lifecycle",
    "build_shared_channel",
    "close_shared_channels",
    "create_grpc_server",
    "current_jwt_payload",
    "db_session",
    "forwarded_jwt",
    "grpc_error_handler",
    "reset_forwarded_jwt",
    "set_forwarded_jwt",
    "start_grpc_server",
    "stop_grpc_server",
]

_EXPORT_MODULES = {
    "GrpcClientMetricsInterceptor": ".metrics",
    "GrpcServerMetricsInterceptor": ".metrics",
    "InternalTokenCallCredentials": ".interceptors",
    "InternalTokenClientInterceptor": ".interceptors",
    "InternalTokenInterceptor": ".interceptors",
    "JwtContextServerInterceptor": ".jwt_forwarding",
    "JwtForwardingClientInterceptor": ".jwt_forwarding",
    "abort_invalid": ".servicer",
    "abort_not_found": ".servicer",
    "build_grpc_lifecycle": ".server",
    "build_shared_channel": ".channels",
    "close_shared_channels": ".channels",
    "create_grpc_server": ".server",
    "current_jwt_payload": ".jwt_forwarding",
    "db_session": ".servicer",
    "forwarded_jwt": ".jwt_forwarding",
    "grpc_error_handler": ".servicer",
    "reset_forwarded_jwt": ".jwt_forwarding",
    "set_forwarded_jwt": ".jwt_forwarding",
    "start_grpc_server": ".server",
    "stop_grpc_server": ".server",
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
