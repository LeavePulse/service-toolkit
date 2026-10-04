from __future__ import annotations

import grpc
import pytest

from service_toolkit.grpc.calls import (
    UPSTREAM_FAILURE_CODES,
    apply_present_fields,
    present_fields,
    translate_grpc_error,
)


class _Unset:
    pass


class _Request:
    pass


def test_apply_present_fields_skips_external_unset_and_maps_none() -> None:
    request = _Request()

    apply_present_fields(
        request,
        unset_type=_Unset,
        none_value="",
        website_url=_Unset(),
        invite_url=None,
        enabled=False,
        title="LeavePulse",
    )

    assert not hasattr(request, "website_url")
    assert request.invite_url == ""
    assert request.enabled is False
    assert request.title == "LeavePulse"


def test_present_fields_returns_filtered_values() -> None:
    assert present_fields(
        unset_type=_Unset,
        none_value="",
        coerce=str,
        skipped=_Unset(),
        cleared=None,
        value=42,
    ) == {"cleared": "", "value": "42"}


def test_apply_present_fields_skips_none_by_default() -> None:
    request = _Request()

    apply_present_fields(request, name=None, slug="server")

    assert not hasattr(request, "name")
    assert request.slug == "server"


def test_apply_present_fields_can_coerce_values() -> None:
    request = _Request()

    apply_present_fields(request, coerce=str, owner_id=42)

    assert request.owner_id == "42"


class _RpcError:
    """Stand-in for ``AioRpcError``; the translator only reads these two."""

    def __init__(self, code: grpc.StatusCode, details: str = "") -> None:
        self._code = code
        self._details = details

    def code(self) -> grpc.StatusCode:
        return self._code

    def details(self) -> str:
        return self._details


@pytest.mark.parametrize("code", sorted(UPSTREAM_FAILURE_CODES, key=lambda c: c.value))
def test_upstream_failures_translate_to_service_unavailable(
    code: grpc.StatusCode,
) -> None:
    error = translate_grpc_error(_RpcError(code), resource="launcher")  # type: ignore[arg-type]

    assert getattr(error, "status_code", None) == 503
    assert "launcher" in str(error)


def test_upstream_failure_keeps_upstream_detail() -> None:
    error = translate_grpc_error(
        _RpcError(grpc.StatusCode.UNAVAILABLE, "connection refused"),  # type: ignore[arg-type]
        resource="launcher",
    )

    assert "connection refused" in str(error)


def test_caller_errors_are_not_reported_as_unavailable() -> None:
    error = translate_grpc_error(
        _RpcError(grpc.StatusCode.INVALID_ARGUMENT, "bad id"),  # type: ignore[arg-type]
        resource="launcher",
    )

    assert getattr(error, "status_code", None) != 503


# --- is_upstream_failure, over a real call --------------------------------------------


_CODES_SAID = {
    grpc.StatusCode.UNAVAILABLE: True,
    grpc.StatusCode.DEADLINE_EXCEEDED: True,
    grpc.StatusCode.RESOURCE_EXHAUSTED: True,
    grpc.StatusCode.INTERNAL: True,
    grpc.StatusCode.UNAUTHENTICATED: False,
    grpc.StatusCode.PERMISSION_DENIED: False,
    grpc.StatusCode.INVALID_ARGUMENT: False,
    grpc.StatusCode.FAILED_PRECONDITION: False,
    grpc.StatusCode.NOT_FOUND: False,
}


@pytest.mark.asyncio
async def test_is_upstream_failure_reads_the_status_of_a_real_call() -> None:
    """Every code, raised by a real server, through `grpc_call`'s translation:
    only the upstream failures say so, whatever Python type they became."""
    from service_toolkit.grpc.calls import grpc_call, is_upstream_failure

    async def fail(request: bytes, context: grpc.aio.ServicerContext) -> bytes:
        await context.abort(grpc.StatusCode[request.decode()], "said by the server")
        return b""  # pragma: no cover - abort raises

    server = grpc.aio.server()
    server.add_generic_rpc_handlers(
        (
            grpc.method_handlers_generic_handler(
                "t.v1.T", {"Fail": grpc.unary_unary_rpc_method_handler(fail)}
            ),
        )
    )
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            method = channel.unary_unary("/t.v1.T/Fail")
            for code, upstream in _CODES_SAID.items():
                with pytest.raises(Exception) as raised:
                    await grpc_call(method, code.name.encode(), timeout=5, resource="t")
                assert is_upstream_failure(raised.value) is upstream, code
                cause = raised.value.__cause__
                assert isinstance(cause, grpc.aio.AioRpcError) and cause.code() is code
                assert is_upstream_failure(cause) is upstream, code
    finally:
        await server.stop(None)


def test_is_upstream_failure_is_false_for_anything_but_a_call() -> None:
    from service_toolkit.grpc.calls import is_upstream_failure

    assert not is_upstream_failure(RuntimeError("a defect"))
    assert not is_upstream_failure(ConnectionError("not raised by a call"))
    wrapped = ValueError("no rpc cause")
    wrapped.__cause__ = KeyError("x")
    assert not is_upstream_failure(wrapped)


def test_the_translation_and_the_predicate_share_one_set() -> None:
    from service_toolkit.grpc import calls

    for code in grpc.StatusCode:
        translated = calls.translate_grpc_error(_RpcError(code), resource="r")  # type: ignore[arg-type]
        says_unavailable = getattr(translated, "status_code", None) == 503
        assert says_unavailable is (code in calls.UPSTREAM_FAILURE_CODES), code
