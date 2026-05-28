"""Shared datetime helpers.

Small, dependency-free time utilities that were being re-implemented across
services (naive-UTC normalisation in particular).
"""

from __future__ import annotations

from datetime import UTC, datetime

__all__ = ["to_naive_utc"]


def to_naive_utc(value: datetime) -> datetime:
    """Return ``value`` as a naive UTC datetime.

    Aware datetimes are converted to UTC then stripped of tzinfo; naive
    datetimes are assumed to already be UTC and returned unchanged. This is the
    shape ClickHouse/Postgres timestamp columns expect across the fleet.
    """
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)
