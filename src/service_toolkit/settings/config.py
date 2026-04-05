"""Reusable settings classes shared across LeavePulse services.

Provides base classes for common configuration blocks (database, internal
token, Redis coordination, gRPC) so that individual services only define their
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
    "DatabaseSettings",
    "GrpcSettings",
    "InternalSettings",
    "RedisCoordinationSettings",
]
