"""Shared transport-facing model bases."""

from __future__ import annotations

from datetime import datetime

import msgspec


class TimestampedStruct(msgspec.Struct, kw_only=True):
    """Transport counterpart for models backed by TimestampMixin."""

    created_at: datetime
    updated_at: datetime


__all__ = ["TimestampedStruct"]
