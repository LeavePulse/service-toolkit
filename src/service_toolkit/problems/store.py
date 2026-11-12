"""Where problems and their history are kept.

``ProblemStore`` is the protocol; ``MemoryProblemStore`` keeps it in a dict for
tests and single-process tools, ``service_toolkit.problems.sql`` keeps it in
Postgres. Both share the decisions below (what an open, an update and a
resolve write), so the two cannot disagree about the model.

Writes are idempotent on the transition id: a re-delivered transition writes
nothing. An update has no id of its own from reactive; it is idempotent by
content instead (the same details write nothing) and gets an id from the
problem and its revision, which is unique by construction.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Protocol

import msgspec

from service_toolkit.ids import generate_id

from .model import Problem, ProblemDetails, ProblemState, ProblemTransition, Severity

__all__ = ["MemoryProblemStore", "ProblemEvent", "ProblemStore", "SEVERITY_RANK"]

#: Panel order: the worst first.
SEVERITY_RANK: dict[Severity, int] = {"critical": 0, "warning": 1, "info": 2}


class ProblemEvent(msgspec.Struct, frozen=True):
    """A transition with the problem it belongs to named, for an events feed."""

    transition: ProblemTransition
    kind: str
    subject_kind: str
    subject_id: str


class ProblemStore(Protocol):
    async def open(
        self, kind: str, subject_kind: str, subject_id: str, details: ProblemDetails, at: datetime, transition_id: str,
    ) -> Problem | None:
        """Open the problem, or, when it is already open, update it. Returns the open problem;
        None when ``transition_id`` was already recorded and nothing is open."""

    async def update(self, kind: str, subject_id: str, details: ProblemDetails, at: datetime) -> Problem | None:
        """Mark an open problem checked at ``at`` and record a change of details. None when nothing is open."""

    async def resolve(self, kind: str, subject_id: str, at: datetime, transition_id: str) -> Problem | None:
        """Resolve the open problem. None when nothing is open or the id was already recorded."""

    async def get_open(self, kind: str, subject_id: str) -> Problem | None: ...

    async def open_problems(
        self,
        *,
        subject_kind: str | None = None,
        subject_id: str | None = None,
        kind: str | None = None,
        severity: Iterable[Severity] | None = None,
    ) -> list[Problem]:
        """Open problems, the worst and oldest first."""

    async def problem(self, problem_id: int) -> tuple[Problem, list[ProblemTransition]] | None:
        """One problem with its whole history, oldest transition first."""

    async def recent(self, *, limit: int = 100, since: datetime | None = None) -> list[ProblemEvent]:
        """The newest transitions across all problems, newest first."""

    async def compact(self, *, before: datetime) -> int:
        """Drop transitions older than ``before`` of problems resolved before it. Returns how many.

        The problem row stays: it still says what was wrong, from when, until
        when. Only the step-by-step history goes.
        """


# ── decisions shared by every store ───────────────────────────────────────


def new_problem(
    problem_id: int, kind: str, subject_kind: str, subject_id: str, details: ProblemDetails, at: datetime,
) -> Problem:
    return Problem(
        id=problem_id,
        kind=kind,
        subject_kind=subject_kind,
        subject_id=subject_id,
        severity=details.severity,
        message=details.message,
        action=details.action,
        evidence=details.evidence,
        first_seen=at,
        last_transition=at,
        last_probe=at,
        observed_generation=details.observed_generation,
    )


def transition(
    transition_row_id: int,
    problem: Problem,
    at: datetime,
    before: ProblemState | None,
    after: ProblemState,
    changes: dict[str, object],
    transition_id: str,
) -> ProblemTransition:
    return ProblemTransition(transition_row_id, problem.id, at, before, after, dict(changes), transition_id)


def updated(problem: Problem, details: ProblemDetails, at: datetime) -> tuple[Problem, dict[str, object]]:
    """The problem after a check at ``at``, and what changed (empty: only the probe time moved)."""
    changes = details.changes(problem.details())
    if not changes:
        return msgspec.structs.replace(problem, last_probe=at), {}
    return (
        msgspec.structs.replace(
            problem,
            severity=details.severity,
            message=details.message,
            action=details.action,
            evidence=details.evidence,
            observed_generation=details.observed_generation,
            last_transition=at,
            last_probe=at,
            revision=problem.revision + 1,
        ),
        changes,
    )


def update_id(problem: Problem) -> str:
    """The id of the update that produced ``problem``'s current revision."""
    return f"problem:{problem.id}:update:{problem.revision}"


def ordered(problems: Iterable[Problem]) -> list[Problem]:
    return sorted(problems, key=lambda p: (SEVERITY_RANK[p.severity], p.first_seen, p.id))


# ── in memory ─────────────────────────────────────────────────────────────


class MemoryProblemStore:
    """A ``ProblemStore`` in a dict. For tests and single-process tools only."""

    def __init__(self, ids: Callable[[], int] = generate_id) -> None:
        self._ids = ids
        self._problems: dict[int, Problem] = {}
        self._open: dict[tuple[str, str], int] = {}
        self._transitions: list[ProblemTransition] = []
        self._seen: set[str] = set()

    def _append(self, row: ProblemTransition) -> None:
        self._transitions.append(row)
        self._seen.add(row.transition_id)

    def _last_state(self, problem_id: int) -> ProblemState | None:
        for row in reversed(self._transitions):
            if row.problem_id == problem_id:
                return row.to_state
        return None

    async def open(
        self, kind: str, subject_kind: str, subject_id: str, details: ProblemDetails, at: datetime, transition_id: str,
    ) -> Problem | None:
        if (kind, subject_id) in self._open:
            return await self.update(kind, subject_id, details, at)
        if transition_id in self._seen:
            return None
        problem = new_problem(self._ids(), kind, subject_kind, subject_id, details, at)
        self._problems[problem.id] = problem
        self._open[(kind, subject_id)] = problem.id
        self._append(transition(self._ids(), problem, at, None, "open", details.as_json(), transition_id))
        return problem

    async def update(self, kind: str, subject_id: str, details: ProblemDetails, at: datetime) -> Problem | None:
        current = await self.get_open(kind, subject_id)
        if current is None:
            return None
        problem, changes = updated(current, details, at)
        self._problems[problem.id] = problem
        if changes:
            before = self._last_state(problem.id)
            self._append(transition(self._ids(), problem, at, before, "updated", changes, update_id(problem)))
        return problem

    async def resolve(self, kind: str, subject_id: str, at: datetime, transition_id: str) -> Problem | None:
        current = await self.get_open(kind, subject_id)
        if current is None or transition_id in self._seen:
            return None
        problem = msgspec.structs.replace(current, resolved_at=at, last_transition=at, last_probe=at)
        self._problems[problem.id] = problem
        del self._open[(kind, subject_id)]
        before = self._last_state(problem.id)
        self._append(transition(self._ids(), problem, at, before, "resolved", {}, transition_id))
        return problem

    async def get_open(self, kind: str, subject_id: str) -> Problem | None:
        problem_id = self._open.get((kind, subject_id))
        return None if problem_id is None else self._problems[problem_id]

    async def open_problems(
        self,
        *,
        subject_kind: str | None = None,
        subject_id: str | None = None,
        kind: str | None = None,
        severity: Iterable[Severity] | None = None,
    ) -> list[Problem]:
        wanted = frozenset(severity) if severity is not None else None
        return ordered(
            p
            for p in (self._problems[i] for i in self._open.values())
            if (subject_kind is None or p.subject_kind == subject_kind)
            and (subject_id is None or p.subject_id == subject_id)
            and (kind is None or p.kind == kind)
            and (wanted is None or p.severity in wanted)
        )

    async def problem(self, problem_id: int) -> tuple[Problem, list[ProblemTransition]] | None:
        problem = self._problems.get(problem_id)
        if problem is None:
            return None
        return problem, [t for t in self._transitions if t.problem_id == problem_id]

    async def recent(self, *, limit: int = 100, since: datetime | None = None) -> list[ProblemEvent]:
        rows = [t for t in self._transitions if since is None or t.at >= since]
        rows.sort(key=lambda t: (t.at, t.id), reverse=True)
        out: list[ProblemEvent] = []
        for row in rows[:limit]:
            p = self._problems[row.problem_id]
            out.append(ProblemEvent(row, p.kind, p.subject_kind, p.subject_id))
        return out

    async def compact(self, *, before: datetime) -> int:
        done = {
            p.id for p in self._problems.values() if p.resolved_at is not None and p.resolved_at < before
        }
        keep: list[ProblemTransition] = []
        for row in self._transitions:
            if row.problem_id in done and row.at < before:
                # The id goes with the row, as it does in a database.
                self._seen.discard(row.transition_id)
            else:
                keep.append(row)
        dropped = len(self._transitions) - len(keep)
        self._transitions = keep
        return dropped
