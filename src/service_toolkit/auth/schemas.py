"""Auth-related shared schemas.

This module is intentionally small and stable. Services are free to wrap it with
more specific domain types.
"""

from __future__ import annotations

try:
    import msgspec
except ModuleNotFoundError as exc:  # pragma: no cover - optional dependency
    if exc.name == "msgspec":
        raise ModuleNotFoundError(
            "Auth helpers require the optional 'auth' extra. "
            "Install with 'pip install service-toolkit[auth]'."
        ) from exc
    raise


class JWTPayload(msgspec.Struct, kw_only=True):
    """Decoded JWT payload (access token).

    North Star: platform permissions live in auth-service (embedded into JWT).

    Keep this schema forward-compatible: unknown claims are ignored and most
    fields are optional.
    """

    sub: str
    jti: str | None = None
    iss: str | None = None
    aud: str | None = None
    exp: int | None = None
    iat: int | None = None
    type: str | None = None
    roles: list[str] = msgspec.field(default_factory=list)
    scope: list[str] = msgspec.field(default_factory=list)
    tenant: str | None = None

    # Platform RBAC claims (bitset).
    platform_perms_bits: int = 0
    platform_perms_version: int | None = None

    # User state claim.
    user_status: str | None = None


__all__ = ["JWTPayload"]
