"""Auth context helpers."""

from __future__ import annotations

from dataclasses import dataclass

from service_toolkit.auth.schemas import JWTPayload


@dataclass(frozen=True, slots=True)
class AuthUser:
    """Authenticated user derived from a verified JWT payload."""

    user_id: int
    tenant_id: str | None
    roles: tuple[str, ...]
    scope: tuple[str, ...]


def build_user(payload: JWTPayload) -> AuthUser:
    """Build an :class:`AuthUser` from a verified JWT payload."""

    return AuthUser(
        user_id=int(payload.sub),
        tenant_id=payload.tenant,
        roles=tuple(payload.roles or []),
        scope=tuple(payload.scope or []),
    )


__all__ = ["AuthUser", "build_user"]
