"""Shared request auth helpers for Litestar handlers."""

from __future__ import annotations

from typing import Annotated

from awesome_errors import AuthRequiredError
from litestar import Request
from litestar.di import Provide
from litestar.params import Dependency

from ..auth import AuthUser

CurrentUser = Annotated[AuthUser, Dependency(skip_validation=True)]


def current_user(request: Request) -> AuthUser | None:
    """Return the authenticated user when JWT middleware attached one."""

    user = request.user
    if isinstance(user, AuthUser):
        return user
    return None


def require_user(
    request: Request,
    *,
    message: str = "Authentication required.",
) -> AuthUser:
    """Return the authenticated user or raise ``AuthRequiredError``."""

    user = current_user(request)
    if user is None:
        raise AuthRequiredError(message)
    return user


def provide_current_user(request: Request) -> AuthUser:
    """Litestar dependency provider for ``CurrentUser`` route params."""

    return require_user(request)


def current_user_dependency(
    *,
    key: str = "current_user",
    use_cache: bool = False,
) -> dict[str, Provide]:
    """Build Litestar dependency mapping for ``CurrentUser`` injection.

    The default mapping exposes both ``current_user`` and ``user`` so handlers
    can use the shorter ``user: CurrentUser`` signature without extra wiring.
    """

    provider = Provide(
        provide_current_user,
        use_cache=use_cache,
        sync_to_thread=False,
    )
    dependency_keys = (key, "user") if key == "current_user" else (key,)
    return {dependency_key: provider for dependency_key in dependency_keys}


__all__ = [
    "CurrentUser",
    "current_user",
    "current_user_dependency",
    "provide_current_user",
    "require_user",
]
