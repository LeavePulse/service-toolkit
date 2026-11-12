"""Feeding a reactor from a pass that already holds the whole state.

A reconcile pass (control-plane style) reads every host, edge or service in one
go; it should not have to wire per-entity events. ``SnapshotFeed`` takes what
the pass knows about each entity of one kind, hands it to the reactor, and
serves as that kind's ``Provider`` from the last pass, so reactive's timers
(``hold``, retries) and its own reconcile read the same view.

What absence means is the important part:

* An entity **missing from a pass** is not known to be fine. The feed has no
  facts for it (``None``, which the reactor reads as UNKNOWN): nothing
  transitions and the clock of a ``hold`` pauses. A detector that sees a host
  as unreachable must say ``reachable=False``; dropping the host from the
  snapshot says nothing.
* An entity **removed** (decommissioned, deleted) is passed in ``removed``.
  The feed answers ``GONE`` for it from then on, and the reactor withdraws it,
  which exits every rule, so its problems resolve.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from service_toolkit.reactive import GONE, EntityRef, Facts, Gone, Reactor

__all__ = ["SnapshotFeed"]


class SnapshotFeed:
    """One entity kind's facts, fed from passes; also its ``Provider``."""

    def __init__(self, reactor: Reactor, subject: str) -> None:
        self.reactor = reactor
        self.subject = subject
        self._facts: dict[str, Facts] = {}
        self._gone: set[str] = set()
        self._ready = False
        reactor.provide(subject, self)

    def ref(self, subject_id: str) -> EntityRef:
        return EntityRef(self.subject, subject_id)

    async def push(
        self,
        snapshot: Mapping[str, Mapping[str, Any]],
        *,
        removed: Iterable[str] = (),
        version: int | None = None,
    ) -> None:
        """Submit one pass: the facts of every entity it saw, and the ids it knows are gone.

        ``version`` orders passes when two writers can race (a monotonic pass
        number that survives a restart, or a source timestamp); without one
        the reactor takes passes in the order they come.
        """
        gone = set(removed)
        unseen = [i for i in self._facts if i not in snapshot and i not in gone]
        for subject_id in (*unseen, *gone):
            self._facts.pop(subject_id, None)
        self._gone |= gone
        for subject_id, fields in snapshot.items():
            if subject_id in gone:
                continue
            self._gone.discard(subject_id)
            self._facts[subject_id] = Facts(self.ref(subject_id), fields, version)
        self._ready = True
        for facts in list(self._facts.values()):
            await self.reactor.changed(facts)
        for subject_id in unseen:
            await self.reactor.unknown(self.ref(subject_id))
        for subject_id in gone:
            await self.reactor.gone(self.ref(subject_id))

    # ── Provider ──────────────────────────────────────────────────────────

    async def facts(self, entity: EntityRef) -> Facts | Gone | None:
        if entity.id in self._gone:
            return GONE
        return self._facts.get(entity.id)

    async def entities(self) -> list[EntityRef]:
        return [self.ref(i) for i in self._facts]

    def ready(self) -> bool:
        return self._ready

    def dependents(self, entity: EntityRef, fields: frozenset[str]) -> Iterable[tuple[EntityRef, frozenset[str]]]:
        return ()
