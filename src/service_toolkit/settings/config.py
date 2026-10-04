"""Reusable settings classes shared across LeavePulse services.

Provides base classes for common configuration blocks (database, internal
token, Redis coordination, gRPC) so that individual services only define their
service-specific settings.

All classes inherit from :class:`msgspec_conf.BaseSettings` and work with
the standard ``BaseSettings.load(prefix=...)`` pattern.
"""

from __future__ import annotations

from msgspec import field
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
    """Internal service-to-service authentication.

    Default ``prefix`` when loading: ``INTERNAL_``.

    ``token`` is the platform token every service holds. ``caller_tokens``
    names the callers this service tells apart, each by its own secret
    (``INTERNAL_CALLER_TOKENS='{"control-service": "..."}'``), for the methods
    its ``CallerPolicy`` lists; see :mod:`service_toolkit.grpc.authorization`.
    The variable's name carries ``TOKEN``, so the control-plane stores and shows
    it as a secret.
    """

    token: str | None = None
    caller_tokens: dict[str, str] = field(default_factory=dict)

    def __repr__(self) -> str:
        """Structure only: whether a token is set and which callers are
        known, never a secret. Settings get logged and printed whole."""
        token = "<set>" if self.token else None
        return (
            f"InternalSettings(token={token!r}, "
            f"caller_tokens={sorted(self.caller_tokens)!r})"
        )

    def __rich_repr__(self):  # type: ignore[no-untyped-def]
        """The same redaction for rich and other pretty-printers, which read
        this instead of ``__repr__``."""
        yield "token", "<set>" if self.token else None
        yield "caller_tokens", sorted(self.caller_tokens)


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


class TracingSettings(BaseSettings):
    """OpenTelemetry trace-export policy.

    Load this class with the ``OTEL_`` prefix. Field names deliberately retain
    the standard OpenTelemetry suffixes, so the typed source remains compatible
    with ``OTEL_EXPORTER_OTLP_*`` and friends rather than inventing a parallel
    configuration vocabulary.
    """

    enabled: bool = False
    exporter_otlp_endpoint: str = "http://tempo:4317"
    exporter_otlp_headers: str = ""
    exporter_otlp_insecure: bool | None = None
    traces_sampler_arg: float = 1.0
    resource_attributes: str = ""
    instrument_httpx: bool = True
    instrument_sqlalchemy: bool = True
    instrument_redis: bool = True


__all__ = [
    "DatabaseSettings",
    "GrpcSettings",
    "InternalSettings",
    "RedisCoordinationSettings",
    "TracingSettings",
]
