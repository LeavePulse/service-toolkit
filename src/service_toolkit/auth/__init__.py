"""Auth helpers shared across services.

These utilities intentionally focus on *mechanics* (JWT validation, JWKS caching,
standard token claims). Domain-specific authorization logic stays in each
service.

Requires: `service-toolkit[auth]`.
"""

from service_toolkit.auth.jwks import JWKSCache
from service_toolkit.auth.schemas import JWTPayload
from service_toolkit.auth.types import AuthUser, build_user
from service_toolkit.auth.verifier import (
    JWTVerificationError,
    JWTVerifier,
    build_shared_jwt_verifier,
)

__all__ = [
    "AuthUser",
    "JWKSCache",
    "JWTPayload",
    "JWTVerificationError",
    "JWTVerifier",
    "build_shared_jwt_verifier",
    "build_user",
]
