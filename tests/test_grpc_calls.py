from __future__ import annotations

import grpc
import pytest

from service_toolkit.grpc.calls import (
    _UPSTREAM_FAILURE_CODES,
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


@pytest.mark.parametrize("code", _UPSTREAM_FAILURE_CODES)
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
