"""JWT verification helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar, cast, overload

try:
    from jose import JWTError, jwt  # type: ignore[import-untyped]
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

PayloadT = TypeVar("PayloadT")


class JWTVerificationError(ValueError):
    """Raised when a JWT cannot be verified."""


class JWTVerifier[PayloadT]:
    """Verify JWTs against auth-service JWKS."""

    def __init__(
        self,
        *,
        jwks_cache: JWKSCache,
        issuer: str | None,
        audience: str | None,
        payload_type: type[PayloadT],
        allowed_types: set[str | None] | None = None,
    ) -> None:
        self._jwks_cache = jwks_cache
        self._issuer = issuer
        self._audience = audience
        self._payload_type = payload_type
        self._allowed_types = allowed_types or {"access"}

    async def verify(self, token: str) -> PayloadT:
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

        decode_options: dict[str, bool] = {}
        audience = (self._audience or "").strip() or None
        issuer = (self._issuer or "").strip() or None
        if audience is None:
            decode_options["verify_aud"] = False
        if issuer is None:
            decode_options["verify_iss"] = False

        try:
            payload = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                issuer=issuer,
                audience=audience,
                options=decode_options or None,
            )
        except JWTError as exc:
            msg = "Token verification failed"
            raise JWTVerificationError(msg) from exc

        try:
            # `strict=False` keeps the schema forward-compatible when auth-service
            # introduces new claims.
            parsed = convert(payload, self._payload_type, strict=False)
        except ValidationError as exc:
            msg = "Token payload is invalid"
            raise JWTVerificationError(msg) from exc

        token_type = cast("str | None", getattr(parsed, "type", None))
        normalized_type = ((token_type or "").strip() or None)
        if normalized_type not in self._allowed_types:
            msg = "Unsupported token type"
            raise JWTVerificationError(msg)

        return parsed


@overload
def build_shared_jwt_verifier(
    *,
    jwks_url: str,
    jwks_ttl_seconds: int,
    http_timeout_seconds: float,
    issuer: str | None,
    audience: str | None,
    allowed_types: set[str | None] | None = None,
) -> JWTVerifier[JWTPayload]: ...


@overload
def build_shared_jwt_verifier(
    *,
    jwks_url: str,
    jwks_ttl_seconds: int,
    http_timeout_seconds: float,
    issuer: str | None,
    audience: str | None,
    payload_type: type[PayloadT],
    allowed_types: set[str | None] | None = None,
) -> JWTVerifier[PayloadT]: ...


def build_shared_jwt_verifier(
    *,
    jwks_url: str,
    jwks_ttl_seconds: int,
    http_timeout_seconds: float,
    issuer: str | None,
    audience: str | None,
    payload_type: type[PayloadT] | None = None,
    allowed_types: set[str | None] | None = None,
) -> JWTVerifier[PayloadT] | JWTVerifier[JWTPayload]:
    """Build a JWT verifier backed by a process-wide shared JWKS cache."""

    from service_toolkit.auth.jwks import JWKSCache

    if payload_type is None:
        return JWTVerifier(
            jwks_cache=JWKSCache.shared(
                url=jwks_url,
                ttl_seconds=int(jwks_ttl_seconds),
                timeout_seconds=float(http_timeout_seconds),
            ),
            issuer=issuer,
            audience=audience,
            payload_type=JWTPayload,
            allowed_types=allowed_types,
        )

    return JWTVerifier(
        jwks_cache=JWKSCache.shared(
            url=jwks_url,
            ttl_seconds=int(jwks_ttl_seconds),
            timeout_seconds=float(http_timeout_seconds),
        ),
        issuer=issuer,
        audience=audience,
        payload_type=payload_type,
        allowed_types=allowed_types,
    )


__all__ = ["JWTVerificationError", "JWTVerifier", "build_shared_jwt_verifier"]
