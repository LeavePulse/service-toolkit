"""JWT verification helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

try:
    from jose import JWTError, jwt
except ModuleNotFoundError as exc:  # pragma: no cover - optional dependency
    if exc.name == "jose":
        raise ModuleNotFoundError(
            "Auth helpers require the optional 'auth' extra. "
            "Install with 'pip install service-toolkit[auth]'."
        ) from exc
    raise

try:
    from msgspec import ValidationError, convert
except ModuleNotFoundError as exc:  # pragma: no cover - optional dependency
    if exc.name == "msgspec":
        raise ModuleNotFoundError(
            "Auth helpers require the optional 'auth' extra. "
            "Install with 'pip install service-toolkit[auth]'."
        ) from exc
    raise

from service_toolkit.auth.schemas import JWTPayload

if TYPE_CHECKING:
    from service_toolkit.auth.jwks import JWKSCache


class JWTVerificationError(ValueError):
    """Raised when a JWT cannot be verified."""


class JWTVerifier:
    """Verify JWTs against auth-service JWKS."""

    def __init__(
        self,
        *,
        jwks_cache: JWKSCache,
        issuer: str,
        audience: str,
        allowed_types: set[str] | None = None,
    ) -> None:
        self._jwks_cache = jwks_cache
        self._issuer = issuer
        self._audience = audience
        self._allowed_types = allowed_types or {"access"}

    async def verify(self, token: str) -> JWTPayload:
        """Verify and decode a JWT token."""

        try:
            header = jwt.get_unverified_header(token)
        except JWTError as exc:
            msg = "Invalid token header"
            raise JWTVerificationError(msg) from exc

        kid = header.get("kid")
        if not kid:
            msg = "Missing kid header"
            raise JWTVerificationError(msg)

        key = await self._jwks_cache.get_key(str(kid))
        if not key:
            msg = "Unknown key id"
            raise JWTVerificationError(msg)

        try:
            payload = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                issuer=self._issuer,
                audience=self._audience,
            )
        except JWTError as exc:
            msg = "Token verification failed"
            raise JWTVerificationError(msg) from exc

        try:
            # `strict=False` keeps the schema forward-compatible when auth-service
            # introduces new claims.
            parsed = convert(payload, JWTPayload, strict=False)
        except ValidationError as exc:
            msg = "Token payload is invalid"
            raise JWTVerificationError(msg) from exc

        token_type = (parsed.type or "").strip()
        if token_type not in self._allowed_types:
            msg = "Unsupported token type"
            raise JWTVerificationError(msg)

        return parsed


__all__ = ["JWTVerificationError", "JWTVerifier"]
