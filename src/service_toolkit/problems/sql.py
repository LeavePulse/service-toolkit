"""Problems in a SQL database (Postgres in production), through SQLAlchemy async.

Two tables per owner, named with the owner's prefix because each domain owns
its own (``network_problems`` / ``network_problem_transitions``):

* ``<prefix>problems``: one row per occurrence. At most one open row per
  (kind, subject_id), enforced by a partial unique index; resolved rows stay.
* ``<prefix>problem_transitions``: append-only history, unique on
  ``transition_id`` so a re-delivered transition cannot be written twice.

The tables are declared on the caller's ``MetaData``, so the service's own
migrations (alembic autogenerate) create and evolve them like any other table.
Requires the ``sqlalchemy`` extra.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import msgspec
from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Row,
    String,
    Table,
    Text,
    case,
    delete,
    exists,
    insert,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from service_toolkit.ids import generate_id

from .model import Action, Problem, ProblemDetails, ProblemState, ProblemTransition, Severity
from .store import ProblemEvent, new_problem, transition, update_id, updated

__all__ = ["ProblemTables", "SqlProblemStore", "problem_tables"]

_JSON = JSON().with_variant(JSONB(), "postgresql")


@dataclass(frozen=True, slots=True)
class ProblemTables:
    problems: Table
    transitions: Table


def problem_tables(metadata: MetaData, prefix: str = "") -> ProblemTables:
    """Declare the two tables on ``metadata``; ``prefix="network_"`` gives ``network_problems``."""
    problems_name = f"{prefix}problems"
    transitions_name = f"{prefix}problem_transitions"
    problems = Table(
        problems_name,
        metadata,
        Column("id", BigInteger, primary_key=True, autoincrement=False),
        Column("kind", String(128), nullable=False),
        Column("subject_kind", String(64), nullable=False),
        Column("subject_id", String(255), nullable=False),
        Column("severity", String(16), nullable=False),
        Column("message", Text, nullable=False),
        Column("action", _JSON, nullable=False),
        Column("evidence", _JSON, nullable=False),
        Column("first_seen", DateTime(timezone=True), nullable=False),
        Column("last_transition", DateTime(timezone=True), nullable=False),
        Column("last_probe", DateTime(timezone=True), nullable=False),
        Column("resolved_at", DateTime(timezone=True), nullable=True),
        Column("observed_generation", BigInteger, nullable=True),
        Column("revision", Integer, nullable=False, default=0),
    )
    # One open occurrence per (kind, subject); resolved ones are history.
    Index(
        f"uq_{problems_name}_open",
        problems.c.kind,
        problems.c.subject_id,
        unique=True,
        postgresql_where=problems.c.resolved_at.is_(None),
        sqlite_where=problems.c.resolved_at.is_(None),
    )
    Index(f"ix_{problems_name}_subject", problems.c.subject_kind, problems.c.subject_id)
    Index(f"ix_{problems_name}_resolved_at", problems.c.resolved_at)
    transitions = Table(
        transitions_name,
        metadata,
        Column("id", BigInteger, primary_key=True, autoincrement=False),
        Column("problem_id", BigInteger, ForeignKey(problems.c.id, ondelete="CASCADE"), nullable=False),
        Column("at", DateTime(timezone=True), nullable=False),
        Column("from_state", String(16), nullable=True),
        Column("to_state", String(16), nullable=False),
        Column("changes", _JSON, nullable=False),
        Column("transition_id", String(255), nullable=False, unique=True),
    )
    Index(f"ix_{transitions_name}_problem", transitions.c.problem_id, transitions.c.at)
    Index(f"ix_{transitions_name}_at", transitions.c.at)
    return ProblemTables(problems, transitions)


def _utc(value: datetime) -> datetime:
    # SQLite hands timestamps back naive; they were written as UTC.
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _problem(row: Row[Any]) -> Problem:
    m = row._mapping
    return Problem(
        id=m["id"],
        kind=m["kind"],
        subject_kind=m["subject_kind"],
        subject_id=m["subject_id"],
        severity=m["severity"],
        message=m["message"],
        action=msgspec.convert(m["action"], Action),
        evidence=dict(m["evidence"]),
        first_seen=_utc(m["first_seen"]),
        last_transition=_utc(m["last_transition"]),
        last_probe=_utc(m["last_probe"]),
        resolved_at=_utc(m["resolved_at"]) if m["resolved_at"] is not None else None,
        observed_generation=m["observed_generation"],
        revision=m["revision"],
    )


def _transition(row: Row[Any]) -> ProblemTransition:
    m = row._mapping
    return ProblemTransition(
        id=m["id"],
        problem_id=m["problem_id"],
        at=_utc(m["at"]),
        from_state=m["from_state"],
        to_state=m["to_state"],
        changes=dict(m["changes"]),
        transition_id=m["transition_id"],
    )


def _row_values(problem: Problem) -> dict[str, Any]:
    return {
        "id": problem.id,
        "kind": problem.kind,
        "subject_kind": problem.subject_kind,
        "subject_id": problem.subject_id,
        "severity": problem.severity,
        "message": problem.message,
        "action": msgspec.to_builtins(problem.action),
        "evidence": problem.evidence,
        "first_seen": problem.first_seen,
        "last_transition": problem.last_transition,
        "last_probe": problem.last_probe,
        "resolved_at": problem.resolved_at,
        "observed_generation": problem.observed_generation,
        "revision": problem.revision,
    }


def _transition_values(row: ProblemTransition) -> dict[str, Any]:
    return {
        "id": row.id,
        "problem_id": row.problem_id,
        "at": row.at,
        "from_state": row.from_state,
        "to_state": row.to_state,
        "changes": row.changes,
        "transition_id": row.transition_id,
    }


class SqlProblemStore:
    """A ``ProblemStore`` over SQLAlchemy async.

    ``begin`` opens a session inside a transaction that commits on exit:
    ``async_sessionmaker(engine).begin`` is exactly that. Every method is one
    transaction, and the open row is read ``FOR UPDATE`` so two writers on one
    problem take turns (reactive already serialises one entity on its leader;
    this covers a handover).
    """

    def __init__(
        self,
        tables: ProblemTables,
        begin: Callable[[], AbstractAsyncContextManager[AsyncSession]],
        ids: Callable[[], int] = generate_id,
    ) -> None:
        self._p = tables.problems
        self._t = tables.transitions
        self._begin = begin
        self._ids = ids

    # ── helpers inside one transaction ────────────────────────────────────

    async def _open_row(self, session: AsyncSession, kind: str, subject_id: str) -> Problem | None:
        result = await session.execute(
            select(self._p)
            .where(self._p.c.kind == kind, self._p.c.subject_id == subject_id, self._p.c.resolved_at.is_(None))
            .with_for_update()
        )
        row = result.first()
        return _problem(row) if row is not None else None

    async def _seen(self, session: AsyncSession, transition_id: str) -> bool:
        result = await session.execute(select(exists().where(self._t.c.transition_id == transition_id)))
        return bool(result.scalar())

    async def _last_state(self, session: AsyncSession, problem_id: int) -> ProblemState | None:
        result = await session.execute(
            select(self._t.c.to_state)
            .where(self._t.c.problem_id == problem_id)
            .order_by(self._t.c.at.desc(), self._t.c.id.desc())
            .limit(1)
        )
        state: ProblemState | None = result.scalar()
        return state

    async def _save(self, session: AsyncSession, problem: Problem) -> None:
        values = _row_values(problem)
        del values["id"]
        await session.execute(update(self._p).where(self._p.c.id == problem.id).values(**values))

    async def _update(
        self, session: AsyncSession, current: Problem, details: ProblemDetails, at: datetime,
    ) -> Problem:
        problem, changes = updated(current, details, at)
        await self._save(session, problem)
        if changes:
            before = await self._last_state(session, problem.id)
            row = transition(self._ids(), problem, at, before, "updated", changes, update_id(problem))
            await session.execute(insert(self._t).values(**_transition_values(row)))
        return problem

    # ── ProblemStore ──────────────────────────────────────────────────────

    async def open(
        self, kind: str, subject_kind: str, subject_id: str, details: ProblemDetails, at: datetime, transition_id: str,
    ) -> Problem | None:
        async with self._begin() as session:
            current = await self._open_row(session, kind, subject_id)
            if current is not None:
                return await self._update(session, current, details, at)
            if await self._seen(session, transition_id):
                return None
            problem = new_problem(self._ids(), kind, subject_kind, subject_id, details, at)
            await session.execute(insert(self._p).values(**_row_values(problem)))
            row = transition(self._ids(), problem, at, None, "open", details.as_json(), transition_id)
            await session.execute(insert(self._t).values(**_transition_values(row)))
            return problem

    async def update(self, kind: str, subject_id: str, details: ProblemDetails, at: datetime) -> Problem | None:
        async with self._begin() as session:
            current = await self._open_row(session, kind, subject_id)
            if current is None:
                return None
            return await self._update(session, current, details, at)

    async def resolve(self, kind: str, subject_id: str, at: datetime, transition_id: str) -> Problem | None:
        async with self._begin() as session:
            current = await self._open_row(session, kind, subject_id)
            if current is None or await self._seen(session, transition_id):
                return None
            problem = msgspec.structs.replace(current, resolved_at=at, last_transition=at, last_probe=at)
            await self._save(session, problem)
            before = await self._last_state(session, problem.id)
            row = transition(self._ids(), problem, at, before, "resolved", {}, transition_id)
            await session.execute(insert(self._t).values(**_transition_values(row)))
            return problem

    async def seen(self, transition_id: str) -> bool:
        async with self._begin() as session:
            return await self._seen(session, transition_id)

    async def get_open(self, kind: str, subject_id: str) -> Problem | None:
        async with self._begin() as session:
            result = await session.execute(
                select(self._p).where(
                    self._p.c.kind == kind, self._p.c.subject_id == subject_id, self._p.c.resolved_at.is_(None),
                )
            )
            row = result.first()
            return _problem(row) if row is not None else None

    async def open_problems(
        self,
        *,
        subject_kind: str | None = None,
        subject_id: str | None = None,
        kind: str | None = None,
        severity: Iterable[Severity] | None = None,
    ) -> list[Problem]:
        p = self._p
        query = select(p).where(p.c.resolved_at.is_(None))
        if subject_kind is not None:
            query = query.where(p.c.subject_kind == subject_kind)
        if subject_id is not None:
            query = query.where(p.c.subject_id == subject_id)
        if kind is not None:
            query = query.where(p.c.kind == kind)
        if severity is not None:
            query = query.where(p.c.severity.in_(list(severity)))
        rank = case({"critical": 0, "warning": 1, "info": 2}, value=p.c.severity, else_=3)
        query = query.order_by(rank, p.c.first_seen, p.c.id)
        async with self._begin() as session:
            result = await session.execute(query)
            return [_problem(row) for row in result.all()]

    async def problem(self, problem_id: int) -> tuple[Problem, list[ProblemTransition]] | None:
        async with self._begin() as session:
            found = (await session.execute(select(self._p).where(self._p.c.id == problem_id))).first()
            if found is None:
                return None
            rows = await session.execute(
                select(self._t).where(self._t.c.problem_id == problem_id).order_by(self._t.c.at, self._t.c.id)
            )
            return _problem(found), [_transition(row) for row in rows.all()]

    async def recent(self, *, limit: int = 100, since: datetime | None = None) -> list[ProblemEvent]:
        t, p = self._t, self._p
        query = (
            select(t, p.c.kind, p.c.subject_kind, p.c.subject_id)
            .join(p, p.c.id == t.c.problem_id)
            .order_by(t.c.at.desc(), t.c.id.desc())
            .limit(limit)
        )
        if since is not None:
            query = query.where(t.c.at >= since)
        async with self._begin() as session:
            result = await session.execute(query)
            return [
                ProblemEvent(_transition(row), row._mapping["kind"], row._mapping["subject_kind"], row._mapping["subject_id"])
                for row in result.all()
            ]

    async def compact(self, *, before: datetime) -> int:
        done = select(self._p.c.id).where(self._p.c.resolved_at.is_not(None), self._p.c.resolved_at < before)
        async with self._begin() as session:
            result = await session.execute(
                delete(self._t).where(self._t.c.at < before, self._t.c.problem_id.in_(done))
            )
            return int(getattr(result, "rowcount", 0) or 0)
