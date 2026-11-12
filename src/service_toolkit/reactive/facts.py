"""What the engine knows about one entity at one moment.

A requirement is evaluated against ``Facts``: an immutable mapping of field
name to value, tagged with the entity it describes and a version. Three rules
shape it, each learnt from a defect elsewhere:

* **Missing is not false.** A field the source did not report reads as
  ``UNKNOWN``, and every operator in ``expr`` carries ``UNKNOWN`` through
  instead of turning it into ``False``. "No evidence" read as "not ok" strips
  access from people whose data has not loaded yet.
* **Versions order the views.** Two views of one entity may arrive out of
  order (a reconnect replays an old one). Each carries a monotonic version, and
  the engine drops one older than the last it applied. ``None`` means the
  source cannot say, and the engine then asks the provider instead.
* **Immutable.** An adapter copies what it needs when the event arrives; a
  library's live object can change under an ``await`` (disnake mutates
  ``after`` in a voice update).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final, final

__all__ = ["UNKNOWN", "EntityRef", "Facts", "Unknown"]


@final
class Unknown:
    """The value of a fact nobody reported. A singleton: compare with ``is``."""

    _instance: Unknown | None = None

    def __new__(cls) -> Unknown:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "UNKNOWN"

    def __bool__(self) -> bool:
        # A truthiness test on UNKNOWN is almost always the bug this type exists
        # to prevent, so it is refused rather than guessed.
        raise TypeError("UNKNOWN has no truth value; compare with `is UNKNOWN`")


UNKNOWN: Final = Unknown()


@dataclass(frozen=True, slots=True)
class EntityRef:
    """Which thing: a kind (``member``, ``host``) and an id within it."""

    kind: str
    id: str

    def __str__(self) -> str:
        return f"{self.kind}:{self.id}"


class Facts(Mapping[str, Any]):
    """An immutable view of one entity's fields, with an optional version."""

    __slots__ = ("_data", "entity", "version")

    def __init__(self, entity: EntityRef, data: Mapping[str, Any], version: int | None = None) -> None:
        self.entity = entity
        self.version = version
        self._data: Mapping[str, Any] = MappingProxyType(dict(data))

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def get(self, key: str, default: Any = UNKNOWN) -> Any:
        return self._data.get(key, default)

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def merged(self, update: Mapping[str, Any], version: int | None = None) -> Facts:
        """A new view with ``update`` laid over this one."""
        return Facts(self.entity, {**self._data, **update}, version if version is not None else self.version)

    def __repr__(self) -> str:
        return f"Facts({self.entity}, {dict(self._data)!r}, version={self.version})"
