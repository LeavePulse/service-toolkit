"""Shared transport-facing model bases."""

from __future__ import annotations

from datetime import datetime

import msgspec
from typing_extensions import dataclass_transform


# ``@dataclass_transform`` re-advertises msgspec's Struct field semantics on this
# intermediate base, so subclasses that pass ``kw_only=True`` type-check (mypy
# otherwise resolves their ``__init_subclass__`` to ``object``'s and rejects the
# keyword). Runtime is unchanged — msgspec still requires explicit ``kw_only=True``
# on each subclass.
@dataclass_transform()
class TimestampedStruct(msgspec.Struct, kw_only=True):
    """Transport counterpart for models backed by TimestampMixin."""

    created_at: datetime
    updated_at: datetime


__all__ = ["TimestampedStruct"]
