"""Reusable settings classes shared across LeavePulse services.

Provides base classes for common configuration blocks (database, internal
token, Redis coordination, gRPC) so that individual services only define their
service-specific settings.

All classes inherit from :class:`msgspec_conf.BaseSettings` and work with
the standard ``BaseSettings.load(prefix=...)`` pattern.
"""

from __future__ import annotations

from msgspec_conf import BaseSettings


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
    """Redis configuration: whether to use it, and where it is.

    Default ``prefix`` when loading: ``REDIS_``.

    The address lives here rather than only inside
    :meth:`service_toolkit.state.redis.RedisSettings.from_env`, which reads the
    environment directly. Two readers of the same ``REDIS_`` prefix meant the
    declared settings knew whether Redis was on but not where it was — so a
    control-plane could see the switch and not the address it has to fill.

    Either spelling works, as ``RedisSettings`` accepts both: a whole ``URL``
    (credentials and database included), or ``HOST``/``PORT``.
    """

    enabled: bool = False
    leader_ttl_seconds: float = 30.0

    url: str | None = None
    host: str | None = None
    port: int | None = None

    @property
    def configured(self) -> bool:
        """Whether an address was given at all, by either spelling."""
        return bool((self.url or "").strip() or (self.host or "").strip())


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
