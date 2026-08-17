"""SQLAlchemy declarative base, mixins and pool defaults shared across services."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

#: Verify a pooled connection before handing it out. The cost is one cheap
#: round-trip per checkout; the alternative is that the FIRST query after the
#: database moved, restarted or dropped the connection fails in front of a
#: user. A pool hands back whatever it cached — it does not re-resolve DNS or
#: notice that the server it dialled is gone.
DEFAULT_POOL_PRE_PING = True

#: Retire a connection after this long, so a pool cannot pin an address
#: indefinitely. Without it a long-lived connection outlives the machine it was
#: opened to: after a database is moved, healthy-looking connections keep
#: pointing at the old host until something forces them closed.
DEFAULT_POOL_RECYCLE_SECONDS = 1800


def utcnow() -> datetime:
    """Return current UTC timestamp."""

    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Base declarative class for all ORM models."""


class TimestampMixin:
    """Mixin providing created/updated timestamps."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )


__all__ = ["Base", "TimestampMixin", "utcnow"]
