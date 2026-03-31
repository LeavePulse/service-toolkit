"""JWT verification helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar, cast, overload

try:
    import httpx
except ModuleNotFoundError as exc:  # pragma: no cover - optional dependency
    if exc.name == "httpx":
        raise ModuleNotFoundError(
            "Auth helpers require the optional 'auth' extra. "
            "Install with 'pip install service-toolkit[auth]'."
        ) from exc
    raise

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
    from msgspec import Struct, ValidationError, convert
except ModuleNotFoundError as exc:  # pragma: no cover - optional dependency
    if exc.name == "msgspec":
        raise ModuleNotFoundError(
            "Auth helpers require the optional 'auth' extra. "
            "Install with 'pip install service-toolkit[auth]'."
        ) from exc
    raise

from service_toolkit.auth.jwks import JWKSUnavailableError
from service_toolkit.auth.schemas import JWTPayload
from service_toolkit.web.http import build_shared_async_client

if TYPE_CHECKING:
    from service_toolkit.auth.jwks import JWKSCache

PayloadT = TypeVar("PayloadT")
_ACTIVE_SESSION_TOKEN_TYPES = frozenset({"access", "ws_access"})


class _IntrospectionResponse(Struct, kw_only=True):
    active: bool
    user_id: str | None = None
    jti: str | None = None


class _AuthIntrospector:
    def __init__(self, *, url: str, timeout_seconds: float) -> None:
        normalized_url = str(url).strip()
        if not normalized_url:
            msg = "Token introspection URL must not be empty."
            raise ValueError(msg)
        normalized_timeout = float(timeout_seconds)
        self._client = build_shared_async_client(
            key=f"service_toolkit.auth.introspect:{normalized_url}:{normalized_timeout}",
            timeout_seconds=normalized_timeout,
        )
        self._url = normalized_url

    async def introspect(self, token: str) -> _IntrospectionResponse:
        try:
            response = await self._client.post(self._url, json={"token": token})
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            msg = "Token introspection is temporarily unavailable"
            raise JWTVerificationError(
                msg,
                code="introspection_unavailable",
                retryable=True,
            ) from exc
        except ValueError as exc:
            msg = "Token introspection failed"
            raise JWTVerificationError(msg) from exc

        try:
            return convert(payload, _IntrospectionResponse)
        except ValidationError as exc:
            msg = "Token introspection payload is invalid"
            raise JWTVerificationError(msg) from exc


class JWTVerificationError(ValueError):
    """Raised when a JWT cannot be verified."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "invalid_token",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


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
        introspect_url: str | None = None,
        introspect_http_timeout_seconds: float = 5.0,
    ) -> None:
        self._jwks_cache = jwks_cache
        self._issuer = issuer
        self._audience = audience
        self._payload_type = payload_type
        self._allowed_types = allowed_types or {"access"}
        normalized_introspect_url = str(introspect_url or "").strip()
        self._introspector = (
            _AuthIntrospector(
                url=normalized_introspect_url,
                timeout_seconds=float(introspect_http_timeout_seconds),
            )
            if normalized_introspect_url
            else None
        )

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

        try:
            key = await self._jwks_cache.get_key(str(kid))
        except JWKSUnavailableError as exc:
            msg = "Token verification is temporarily unavailable"
            raise JWTVerificationError(
                msg,
                code="jwks_unavailable",
                retryable=True,
            ) from exc
        if not key:
            msg = "Unknown key id"
            raise JWTVerificationError(msg, code="unknown_key_id")

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
            raise JWTVerificationError(msg, code="token_verification_failed") from exc

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

        payload_user_status = cast("str | None", getattr(parsed, "user_status", None))
        if (
            self._introspector is not None
            and normalized_type in _ACTIVE_SESSION_TOKEN_TYPES
            and payload_user_status is not None
        ):
            introspection = await self._introspector.introspect(token)
            if not introspection.active:
                msg = "Token session is not active"
                raise JWTVerificationError(msg)

            payload_sub = cast("str | None", getattr(parsed, "sub", None))
            if (
                introspection.user_id
                and payload_sub
                and introspection.user_id != payload_sub
            ):
                msg = "Token introspection subject mismatch"
                raise JWTVerificationError(msg)

            payload_jti = cast("str | None", getattr(parsed, "jti", None))
            if introspection.jti and payload_jti and introspection.jti != payload_jti:
                msg = "Token introspection session mismatch"
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
    introspect_url: str | None = None,
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
    introspect_url: str | None = None,
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
    introspect_url: str | None = None,
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
            introspect_url=introspect_url,
            introspect_http_timeout_seconds=float(http_timeout_seconds),
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
        introspect_url=introspect_url,
        introspect_http_timeout_seconds=float(http_timeout_seconds),
    )


__all__ = ["JWTVerificationError", "JWTVerifier", "build_shared_jwt_verifier"]
