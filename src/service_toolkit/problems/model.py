"""What a problem is: its declaration, its typed action, and its stored shape.

A ``ProblemKind`` is declared once, beside the code that knows the domain. Its
``requirement`` is a reactive requirement over the subject's facts: while it is
true, the problem exists. Everything else the kind declares is derived from the
same facts: a human ``message``, a ``severity``, what the system is doing about
it (``action``) and a little structured ``evidence``.

Field names follow Kubernetes conditions: ``kind`` is the machine ``reason``
(CamelCase, stable, what a panel filters on), ``message`` is the sentence a
person reads, ``last_transition`` is when the problem last *changed* and
``last_probe`` when it was last *checked*, and ``observed_generation`` is the
generation of the subject's desired state that was looked at.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, get_args

import msgspec

from service_toolkit.reactive import UNKNOWN, Explanation, Expr, Facts, HeldFor, Latch, Mode, Requirement, Truth, Unknown, held_for

__all__ = [
    "ACTION_TYPES",
    "GENERATION_FIELD",
    "PROBLEM_STATES",
    "SEVERITIES",
    "Action",
    "ActionType",
    "NeedsOperator",
    "Nothing",
    "Problem",
    "ProblemDetails",
    "ProblemKind",
    "ProblemRegistry",
    "ProblemState",
    "ProblemTransition",
    "Remedy",
    "Severity",
    "Waiting",
]

Severity = Literal["info", "warning", "critical"]
#: The severities, lowest first, as values (the Literal's own arguments).
SEVERITIES: tuple[Severity, ...] = get_args(Severity)
#: The ``type`` tag of each ``Action`` variant.
ActionType = Literal["remedy", "waiting", "needs_operator", "nothing"]
ACTION_TYPES: tuple[ActionType, ...] = get_args(ActionType)
ProblemState = Literal["open", "updated", "resolved"]
PROBLEM_STATES: tuple[ProblemState, ...] = get_args(ProblemState)

#: The fact a subject's desired-state generation is read from. A subject whose
#: facts carry it lets a reader tell "not seen yet" from "seen and failing".
GENERATION_FIELD = "generation"

_REASON = re.compile(r"^[A-Z][A-Za-z0-9]*$")


# ── what the system is doing about it ────────────────────────────────────


class Remedy(msgspec.Struct, frozen=True, tag_field="type", tag="remedy"):
    """The system is fixing it by itself: ``kind`` names the remedy, ``at`` when it runs or ran."""

    kind: str
    at: datetime | None = None


class Waiting(msgspec.Struct, frozen=True, tag_field="type", tag="waiting"):
    """Nothing to do yet: the system waits for something (a handshake, a lease).

    ``for_`` says what in a sentence; ``waiting_id`` names it as a stable key
    (the counterpart of ``NeedsOperator.action_id``), so a panel can word it
    itself. Empty in what was stored before it existed.
    """

    for_: str = msgspec.field(name="for")
    until: datetime | None = None
    waiting_id: str = ""


class NeedsOperator(msgspec.Struct, frozen=True, tag_field="type", tag="needs_operator"):
    """Only a person can fix it. ``action_id`` is what a panel button invokes."""

    action_id: str
    label: str


class Nothing(msgspec.Struct, frozen=True, tag_field="type", tag="nothing"):
    """The system is not doing anything about it, and says so."""


Action = Remedy | Waiting | NeedsOperator | Nothing


# ── stored shape ─────────────────────────────────────────────────────────


class ProblemDetails(msgspec.Struct, frozen=True):
    """The part of a problem derived from facts; a change to it is an update."""

    severity: Severity
    message: str
    action: Action
    evidence: dict[str, Any]
    observed_generation: int | None = None

    def changes(self, before: ProblemDetails) -> dict[str, Any]:
        """The fields that differ from ``before``, as JSON-ready values."""
        mine = self.as_json()
        theirs = before.as_json()
        return {name: value for name, value in mine.items() if theirs.get(name) != value}

    def as_json(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "message": self.message,
            "action": msgspec.to_builtins(self.action),
            "evidence": self.evidence,
            "observed_generation": self.observed_generation,
        }


class Problem(msgspec.Struct, frozen=True):
    """One occurrence of a problem kind on one subject, open or resolved."""

    id: int
    kind: str
    subject_kind: str
    subject_id: str
    severity: Severity
    message: str
    action: Action
    evidence: dict[str, Any]
    first_seen: datetime
    last_transition: datetime
    last_probe: datetime
    resolved_at: datetime | None = None
    observed_generation: int | None = None
    # Bumps on every recorded update; part of the update's transition id.
    revision: int = 0

    @property
    def is_open(self) -> bool:
        return self.resolved_at is None

    def details(self) -> ProblemDetails:
        return ProblemDetails(self.severity, self.message, self.action, self.evidence, self.observed_generation)


class ProblemTransition(msgspec.Struct, frozen=True):
    """One append-only line of a problem's history."""

    id: int
    problem_id: int
    at: datetime
    from_state: ProblemState | None
    to_state: ProblemState
    # The fields that changed and their new values (all of them on open).
    changes: dict[str, Any]
    # The idempotency key: a re-delivered transition writes nothing.
    transition_id: str


# ── declaration ──────────────────────────────────────────────────────────


def _no_action(facts: Facts) -> Action:
    return Nothing()


def _json_ready(value: Any) -> Any:
    # Evidence is stored as JSON; anything msgspec cannot encode is kept as
    # its text rather than refused, since evidence must never block a problem.
    def hook(obj: Any) -> Any:
        return None if obj is UNKNOWN else str(obj)

    return msgspec.to_builtins(value, enc_hook=hook, order="sorted")


def _reads(explanation: Explanation) -> dict[str, Any]:
    out: dict[str, Any] = {}
    stack = [explanation]
    while stack:
        node = stack.pop()
        out.update(node.reads)
        stack.extend(node.children)
    return out


def _default_evidence(facts: Facts, explanation: Explanation) -> Mapping[str, Any]:
    """Every fact the requirement read: the minimum a reader needs to check it."""
    return _reads(explanation)


@dataclass(frozen=True, slots=True)
class ProblemKind:
    """A declared kind of problem: when it exists and how it reads.

    ``requirement`` is TRUE while the problem exists. ``hold`` (seconds, or a
    callable read at every evaluation) makes it wait that long before opening,
    with ``grace`` forgiving a shorter break, so noise does not open problems.
    ``refresh`` re-checks an open problem on a timer even when no facts arrive.
    ``reads`` names facts that ``message``/``severity``/``action``/``evidence``
    look at beyond the requirement, so a change to them updates the problem.
    ``mode="shadow"`` evaluates and logs transitions without writing problems,
    for running a kind beside the detector it replaces.
    """

    kind: str
    subject: str
    requirement: Requirement
    message: Callable[[Facts], str]
    severity: Severity | Callable[[Facts], Severity] = "warning"
    action: Callable[[Facts], Action] = _no_action
    evidence: Callable[[Facts, Explanation], Mapping[str, Any]] = _default_evidence
    hold: float | Callable[[], float] | None = None
    grace: float = 0.0
    refresh: float = 0.0
    reads: frozenset[str] = field(default_factory=frozenset)
    mode: Mode = "live"

    def __post_init__(self) -> None:
        if not _REASON.match(self.kind):
            raise ValueError(f"problem kind {self.kind!r} must be a CamelCase reason, e.g. 'RoadHomeMissing'")
        if self.hold is not None and isinstance(self.requirement, HeldFor | Latch):
            # held_for and latch wrap a requirement and cannot nest.
            raise ValueError(f"problem kind {self.kind!r}: give `hold` or a timed requirement, not both")

    @property
    def rule_name(self) -> str:
        """The reactive edge rule that opens and resolves this kind."""
        return f"problem:{self.kind}"

    @property
    def detail_rule_name(self) -> str:
        """The reactive converge rule that keeps an open problem's details current."""
        return f"problem:{self.kind}:details"

    def edge_requirement(self) -> Requirement:
        requirement = self.requirement
        if self.hold is None or not isinstance(requirement, Expr):
            return requirement
        return held_for(requirement, seconds=self.hold, grace=self.grace)

    def fields(self) -> frozenset[str]:
        return self.requirement.fields() | self.reads | {GENERATION_FIELD}

    def explain(self, facts: Facts) -> Explanation:
        """The requirement now, without time: what the facts say this instant."""
        requirement = self.requirement
        if isinstance(requirement, HeldFor):
            return requirement.inner.explain(facts)
        if isinstance(requirement, Latch):
            enter, leave = requirement.enter.explain(facts), requirement.exit.explain(facts)
            # Inside the latch the problem lasts until `exit` holds.
            value: Truth = UNKNOWN if leave.value is UNKNOWN else not leave.value
            return Explanation("latched until exit", value, children=[enter, leave])
        return requirement.explain(facts)

    def details(self, facts: Facts, explanation: Explanation | None = None) -> ProblemDetails:
        explained = explanation if explanation is not None else self.explain(facts)
        severity = self.severity(facts) if callable(self.severity) else self.severity
        generation = facts.get(GENERATION_FIELD)
        return ProblemDetails(
            severity=severity,
            message=self.message(facts),
            action=self.action(facts),
            evidence=_json_ready(dict(self.evidence(facts, explained))),
            observed_generation=generation if isinstance(generation, int) and not isinstance(generation, bool) else None,
        )

    def current(self, facts: Facts) -> ProblemDetails | Unknown:
        """Details while the facts say the problem is there; UNKNOWN otherwise.

        Not true is also UNKNOWN here: a problem about to resolve keeps the
        details it had, instead of an update phrased over facts that no longer
        describe it.
        """
        explained = self.explain(facts)
        if explained.value is not True:
            return UNKNOWN
        return self.details(facts, explained)


class ProblemRegistry:
    """The one place that lists every problem kind a service declares."""

    def __init__(self, kinds: Iterable[ProblemKind] = ()) -> None:
        self._kinds: dict[str, ProblemKind] = {}
        for kind in kinds:
            self.add(kind)

    def add(self, kind: ProblemKind) -> ProblemKind:
        if kind.kind in self._kinds:
            raise ValueError(f"problem kind {kind.kind!r} is declared twice")
        self._kinds[kind.kind] = kind
        return kind

    def get(self, kind: str) -> ProblemKind | None:
        return self._kinds.get(kind)

    def for_subject(self, subject: str) -> list[ProblemKind]:
        return [k for k in self._kinds.values() if k.subject == subject]

    def subjects(self) -> frozenset[str]:
        return frozenset(k.subject for k in self._kinds.values())

    def __iter__(self) -> Iterator[ProblemKind]:
        return iter(self._kinds.values())

    def __len__(self) -> int:
        return len(self._kinds)

    def __contains__(self, kind: object) -> bool:
        return kind in self._kinds
