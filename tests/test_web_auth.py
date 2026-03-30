from __future__ import annotations

from types import SimpleNamespace
from typing import Annotated, get_args, get_origin

import pytest
from litestar import Litestar, get
from awesome_errors import AuthRequiredError
from litestar.di import Provide

from service_toolkit.auth import AuthUser
from service_toolkit.web.auth import (
    CurrentUser,
    current_user,
    current_user_dependency,
    provide_current_user,
    require_user,
)


def _request_with_user(user: object | None) -> SimpleNamespace:
    return SimpleNamespace(user=user)


def test_current_user_returns_authenticated_user() -> None:
    user = AuthUser(user_id=7, tenant_id=None, roles=("admin",), scope=("profile",))

    assert current_user(_request_with_user(user)) is user


def test_current_user_returns_none_for_anonymous_request() -> None:
    assert current_user(_request_with_user(None)) is None


def test_require_user_raises_for_anonymous_request() -> None:
    with pytest.raises(AuthRequiredError, match="Authentication required."):
        require_user(_request_with_user(None))


def test_provide_current_user_returns_authenticated_user() -> None:
    user = AuthUser(user_id=11, tenant_id="tenant", roles=(), scope=())

    provided = provide_current_user(_request_with_user(user))

    assert provided is user


def test_current_user_dependency_registers_expected_provider() -> None:
    dependencies = current_user_dependency()

    assert list(dependencies) == ["current_user", "user"]
    provider = dependencies["current_user"]
    user_provider = dependencies["user"]
    assert isinstance(provider, Provide)
    assert isinstance(user_provider, Provide)
    assert user_provider is not provider
    assert provider.dependency is provide_current_user
    assert user_provider.dependency is not provide_current_user
    assert provider.use_cache is False
    assert provider.sync_to_thread is False


def test_current_user_dependency_registers_without_duplicate_provider_error() -> None:
    @get("/me")
    async def handler(user: CurrentUser, current_user: CurrentUser) -> dict[str, int]:
        return {
            "user_id": int(user.user_id),
            "current_user_id": int(current_user.user_id),
        }

    app = Litestar(route_handlers=[handler], dependencies=current_user_dependency())

    assert app is not None


def test_current_user_annotation_uses_dependency_marker() -> None:
    assert get_origin(CurrentUser) is Annotated

    annotation, dependency = get_args(CurrentUser)
    assert annotation is AuthUser
    assert dependency.skip_validation is True
