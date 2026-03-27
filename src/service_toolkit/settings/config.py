"""Reusable settings classes shared across LeavePulse services.

Provides base classes for common configuration blocks (database, auth, internal
token, Redis coordination) so that individual services only define their
service-specific settings.

All classes inherit from :class:`env_settings.BaseSettings` and work with
the standard ``BaseSettings.load(prefix=...)`` pattern.
"""

from __future__ import annotations

from env_settings import BaseSettings


class DatabaseSettings(BaseSettings):
    """PostgreSQL connection settings.

    Default ``prefix`` when loading: ``POSTGRES_``.
    """

    host: str = "localhost"
    port: int = 5432
    user: str = "postgres"
    password: str = "postgres"
    name: str = "app"
    url: str | None = None

    @property
    def connection_url(self) -> str:
        """Build an asyncpg connection URL, preferring an explicit *url*."""
        if self.url:
            return self.url
        return (
            f"postgresql+asyncpg://{self.user}:{self.password}"
            f"@{self.host}:{self.port}/{self.name}"
        )


class AuthSettings(BaseSettings):
    """Auth-service integration settings (JWKS for JWT validation).

    Default ``prefix`` when loading: ``AUTH_``.
    """

    base_url: str = "http://auth-service:8000"
    grpc_target: str = "auth-service:50000"
    jwks_url: str | None = None
    jwks_cache_ttl_seconds: int = 3600
    http_timeout_seconds: float = 5.0
    issuer: str = "leavepulse-auth"
    audience: str = "leavepulse.api"

    @property
    def resolved_jwks_url(self) -> str:
        """Return the JWKS endpoint, derived from ``base_url`` when needed."""
        if self.jwks_url:
            return self.jwks_url
        base = self.base_url.rstrip("/")
        return f"{base}/auth/.well-known/jwks.json"

    @property
    def resolved_introspect_url(self) -> str:
        """Return the token introspection endpoint derived from ``base_url``."""
        base = self.base_url.rstrip("/")
        return f"{base}/auth/introspect"


class InternalSettings(BaseSettings):
    """Internal service-to-service authentication token.

    Default ``prefix`` when loading: ``INTERNAL_``.
    """

    token: str | None = None


class RedisCoordinationSettings(BaseSettings):
    """Optional Redis configuration used for leader election / coordination.

    Default ``prefix`` when loading: ``REDIS_``.
    """

    enabled: bool = False
    leader_ttl_seconds: float = 30.0


class GrpcSettings(BaseSettings):
    """gRPC server configuration.

    Default ``prefix`` when loading: ``GRPC_``.
    """

    port: int = 50051
    reflection_enabled: bool = True


__all__ = [
    "AuthSettings",
    "DatabaseSettings",
    "GrpcSettings",
    "InternalSettings",
    "RedisCoordinationSettings",
]
