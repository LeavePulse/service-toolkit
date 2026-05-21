from __future__ import annotations

from service_toolkit.grpc.calls import apply_present_fields


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


def test_apply_present_fields_skips_none_by_default() -> None:
    request = _Request()

    apply_present_fields(request, name=None, slug="server")

    assert not hasattr(request, "name")
    assert request.slug == "server"


def test_apply_present_fields_can_coerce_values() -> None:
    request = _Request()

    apply_present_fields(request, coerce=str, owner_id=42)

    assert request.owner_id == "42"
