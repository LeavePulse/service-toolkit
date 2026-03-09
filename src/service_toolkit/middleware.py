"""Shared JWT authentication middleware for LeavePulse services.

Replaces the duplicated ``middleware/authz.py`` found in most services.

Usage::

    from service_toolkit.middleware import JWTAuthMiddleware
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from awesome_errors import AuthInvalidTokenError, AuthPermissionDeniedError
from litestar.middleware.authentication import (
    AbstractAuthenticationMiddleware,
    AuthenticationResult,
)

from .auth import JWTVerificationError, build_user
from .logging import bind_log_user_id

if TYPE_CHECKING:
    from collections.abc import Sequence

    from litestar import Litestar
    from litestar.connection import ASGIConnection
    from litestar.types import Method

    from .auth import JWTVerifier


class JWTAuthMiddleware(AbstractAuthenticationMiddleware):
    """JWT authentication middleware with optional auth requirement.

    Parameters
    ----------
    jwt_verifier:
        Verifier instance (from :func:`service_toolkit.auth.build_shared_jwt_verifier`).
    require_auth:
        If ``True`` (default ``False``), raise :class:`AuthRequiredError` when
        no Bearer token is present.  When ``False``, unauthenticated requests
        pass through with ``user=None``.
    """

    def __init__(
        self,
        app: Litestar,
        *,
        jwt_verifier: JWTVerifier,
        require_auth: bool = False,
        exclude: str | list[str] | None = None,
        exclude_from_auth_key: str = "exclude_from_auth",
        exclude_http_methods: Sequence[Method] | None = None,
    ) -> None:
        super().__init__(
            app,
            exclude=exclude,
            exclude_from_auth_key=exclude_from_auth_key,
            exclude_http_methods=exclude_http_methods,
        )
        self._jwt_verifier = jwt_verifier
        self._require_auth = require_auth

    async def authenticate_request(
        self, connection: ASGIConnection
    ) -> AuthenticationResult:
        auth_header = connection.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            if self._require_auth:
                from awesome_errors import AuthRequiredError

                raise AuthRequiredError("Missing or invalid Bearer token.")
            return AuthenticationResult(user=None, auth=None)

        token = auth_header.split(" ", 1)[1]
        try:
            payload = await self._jwt_verifier.verify(token)
        except JWTVerificationError as exc:
            raise AuthInvalidTokenError("Invalid or expired token.") from exc

        status = (getattr(payload, "user_status", None) or "").strip().lower()
        if status and status != "active":
            raise AuthPermissionDeniedError("Account is not active.")

        try:
            user = build_user(payload)
        except ValueError as exc:
            raise AuthInvalidTokenError("Invalid token subject.") from exc
        bind_log_user_id(user.user_id)

        return AuthenticationResult(user=user, auth=payload)


__all__ = ["JWTAuthMiddleware"]
