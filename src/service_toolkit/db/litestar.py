"""Litestar + Advanced-Alchemy database configuration factory.

Eliminates the duplicated ``db/config.py`` boilerplate found across services.
Each service only needs to call :func:`build_db_config` with its settings object.

Usage in a service::

    from service_toolkit.db.litestar import build_db_config

    db = build_db_config(
        connection_string=settings.database_url,
        echo=settings.sqlalchemy_echo,
    )

    # db.sqlalchemy_config  – pass to SQLAlchemyPlugin
    # db.engine_config      – raw EngineConfig
    # db.create_session_maker() – async_sessionmaker[AsyncSession]
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from advanced_alchemy.config.asyncio import AsyncSessionConfig  # type: ignore[import-not-found]
from advanced_alchemy.extensions.litestar import (  # type: ignore[import-not-found]
    SQLAlchemyAsyncConfig,
)
from advanced_alchemy.extensions.litestar.plugins.init.config.engine import (
    EngineConfig,  # type: ignore[import-not-found]
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@dataclass(frozen=True, slots=True)
class DBConfig:
    """Immutable container holding the SQLAlchemy objects a service needs."""

    engine_config: EngineConfig
    sqlalchemy_config: SQLAlchemyAsyncConfig

    def create_session_maker(self) -> async_sessionmaker[AsyncSession]:
        """Return an async session factory bound to the service engine."""
        return cast(
            async_sessionmaker[AsyncSession],
            self.sqlalchemy_config.create_session_maker(),
        )


def build_db_config(
    *,
    connection_string: str,
    echo: bool = False,
) -> DBConfig:
    """Create the standard database configuration used by all services.

    Parameters
    ----------
    connection_string:
        Full async database URL (e.g. ``postgresql+asyncpg://…``).
    echo:
        Whether to echo SQL statements (maps to ``EngineConfig.echo``).
    """
    engine_config = EngineConfig(echo=echo)
    sqlalchemy_config = SQLAlchemyAsyncConfig(
        connection_string=connection_string,
        engine_config=engine_config,
        # With AsyncSession, attribute access after commit must not trigger
        # implicit IO.
        session_config=AsyncSessionConfig(expire_on_commit=False),
    )
    return DBConfig(engine_config=engine_config, sqlalchemy_config=sqlalchemy_config)


__all__ = ["DBConfig", "build_db_config"]
