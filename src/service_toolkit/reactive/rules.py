"""The two kinds of rule, and what an edge handler is told.

``ConvergeRule`` keeps a desired state: it is told what the world should be
and makes it so, idempotently, as often as the engine likes. ``EdgeRule`` acts
once per transition (enter or exit) and is told a stable transition id to pass
on as an idempotency key, because delivery is at least once.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any, Literal

from .expr import Requirement
from .facts import EntityRef, Facts

__all__ = ["ConvergeRule", "EdgeRule", "Mode", "Transition"]

Mode = Literal["live", "shadow"]


@dataclass(frozen=True, slots=True)
class Transition:
    """One crossing of a rule for one entity."""

    id: str
    rule: str
    entity: EntityRef
    kind: Literal["enter", "exit"]
    epoch: int
    at: float


EdgeHandler = Callable[[EntityRef, Facts, Transition], Awaitable[None]]


@dataclass(slots=True)
class EdgeRule:
    name: str
    kind: str
    requirement: Requirement
    on_enter: EdgeHandler | None = None
    on_exit: EdgeHandler | None = None
    # A change commits only if it still holds this long after it was seen.
    settle: float = 0.0
    # "silent": the first evaluation of an entity records, never fires.
    initial: Literal["silent", "fire"] = "silent"
    # Run on_enter again every this many seconds while the requirement holds
    # (a heartbeat: "and every hour after, while they stay").
    repeat: float = 0.0
    mode: Mode = "live"

    def fields(self) -> frozenset[str]:
        return self.requirement.fields()


@dataclass(slots=True)
class ConvergeRule:
    name: str
    kind: str
    # What the world should be for these facts; UNKNOWN means "do not touch".
    desired: Callable[[Facts], Any]
    # What the world is; UNKNOWN when it cannot be read.
    observe: Callable[[EntityRef], Awaitable[Any]]
    apply: Callable[[EntityRef, Any], Awaitable[None]]
    reads: frozenset[str]
    # Called when the entity leaves the rule's scope or the rule is retired.
    withdraw: Callable[[EntityRef], Awaitable[None]] | None = None
    # The resource keys this rule writes; a second owner of a key is refused.
    owns: Callable[[EntityRef], Iterable[str]] | None = None
    # Read back after apply and count a mismatch as a failure.
    verify: bool = True
    mode: Mode = "live"

    def fields(self) -> frozenset[str]:
        return self.reads
