from __future__ import annotations

import asyncio
from inspect import signature
from types import SimpleNamespace

import pytest

from service_toolkit import (
    ExpansionLoader,
    ProjectionSpec,
    ResponsePolicy,
    request_projection,
)
from service_toolkit.web import with_projection


def test_projection_spec_defaults_to_full_response_when_fields_are_absent() -> None:
    projection = ProjectionSpec.from_query_params()

    assert projection.is_default is True
    assert projection.has("owner") is True
    assert projection.needs("owner.username") is True
    assert projection.includes("owner") is False


def test_projection_spec_tracks_requested_fields_and_descendants() -> None:
    projection = ProjectionSpec.from_query_params(
        fields="owner.username, stats",
        include="moderation_warnings",
        exclude="owner.email",
    )

    assert projection.has("owner") is False
    assert projection.needs("owner") is True
    assert projection.has("stats") is True
    assert projection.includes("moderation_warnings") is True
    assert projection.needs("moderation_warnings.detail") is True
    assert projection.needs("owner.email") is False


def test_projection_spec_restrict_to_keeps_only_allowed_paths() -> None:
    projection = ProjectionSpec.from_query_params(
        fields="owner.username,stats,display_server",
        include="servers,owner",
        exclude="owner.email,display_server",
    ).restrict_to(("owner", "servers"))

    assert projection.has("stats") is False
    assert projection.needs("display_server") is False
    assert projection.needs("owner") is True
    assert projection.includes("servers") is True
    assert projection.needs("owner.email") is False


def test_projection_spec_child_inherits_full_branch_when_parent_requested() -> None:
    projection = ProjectionSpec.from_query_params(fields="owner", exclude="owner.email")
    child = projection.child("owner")

    assert child.field_restricted is False
    assert child.has("username") is True
    assert child.needs("email") is False


def test_projection_spec_default_mode_supports_exclude() -> None:
    projection = ProjectionSpec.from_query_params(exclude="moderation_warnings")

    assert projection.has("subservers") is True
    assert projection.needs("moderation_warnings") is False


def test_response_policy_supports_parent_allows_and_child_denies() -> None:
    policy = ResponsePolicy(
        allowed_fields=frozenset({"owner", "moderation_warnings"}),
        denied_fields=frozenset({"owner.email"}),
    )

    assert policy.can_view("owner") is True
    assert policy.can_view("owner.username") is True
    assert policy.can_view("owner.email") is False
    assert policy.can_view("owner.email.domain") is False
    assert policy.filter_allowed(["owner", "owner.email", "moderation_warnings"]) == [
        "owner",
        "moderation_warnings",
    ]


def test_response_policy_allowing_all_applies_denied_paths() -> None:
    policy = ResponsePolicy.allowing_all(denied_fields="owner.email,moderation_warnings")

    assert policy.can_view("owner.username") is True
    assert policy.can_view("owner.email") is False
    assert policy.can_view("moderation_warnings.detail") is False


def test_request_projection_reads_request_and_state_projection() -> None:
    direct_projection = ProjectionSpec.from_query_params(fields="owner")
    request_with_projection = SimpleNamespace(
        projection=direct_projection,
        state=SimpleNamespace(),
    )
    assert request_projection(request_with_projection) is direct_projection

    state_projection = ProjectionSpec.from_query_params(include="servers")
    request_with_state_projection = SimpleNamespace(
        state=SimpleNamespace(projection=state_projection),
    )
    assert request_projection(request_with_state_projection) is state_projection

    empty_request = SimpleNamespace()
    assert request_projection(empty_request).is_default is True


@pytest.mark.asyncio
async def test_expansion_loader_memoizes_async_lookups() -> None:
    loader = ExpansionLoader()
    calls = 0

    async def _load() -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return "value"

    assert await loader.get_or_load("owner", _load) == "value"
    assert await loader.get_or_load("owner", _load) == "value"
    assert calls == 1


@pytest.mark.asyncio
async def test_with_projection_adds_query_params_and_attaches_projection() -> None:
    request = SimpleNamespace(state=SimpleNamespace())

    @with_projection(allowed_paths=("owner", "servers"))
    async def handler(*, request: object) -> str:
        projection = getattr(request, "projection")
        assert isinstance(projection, ProjectionSpec)
        assert projection.needs("owner") is True
        assert projection.needs("servers") is False
        assert getattr(request.state, "projection") is projection
        return "ok"

    handler_signature = signature(handler)
    assert "fields" in handler_signature.parameters
    assert "include" in handler_signature.parameters
    assert "exclude" in handler_signature.parameters

    result = await handler(
        request=request,
        fields="owner",
        exclude="servers",
    )

    assert result == "ok"
