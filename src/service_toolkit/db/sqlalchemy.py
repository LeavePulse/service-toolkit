"""SQLAlchemy declarative base and mixins shared across services."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


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

