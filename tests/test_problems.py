from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import MetaData, create_engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateIndex, CreateTable

from service_toolkit.problems import (
    MemoryProblemStore,
    NeedsOperator,
    Nothing,
    Problem,
    ProblemDetails,
    ProblemKind,
    ProblemRegistry,
    ProblemStore,
    Remedy,
    Waiting,
)
from service_toolkit.problems.sql import SqlProblemStore, problem_tables
from service_toolkit.reactive import Fields, held_for

F = Fields()
H = "host"


class Ids:
    def __init__(self) -> None:
        self.n = 0

    def __call__(self) -> int:
        self.n += 1
        return self.n


class _Session:
    """Just the part of AsyncSession the store uses, over a sync Session: the
    repo has no async SQLite driver, and the statements are what is tested."""

    def __init__(self, session: Session) -> None:
        self._session = session

    async def execute(self, statement: Any, params: Any = None) -> Any:
        return self._session.execute(statement, params)


@pytest.fixture(params=["memory", "sql"])
def store(request: pytest.FixtureRequest) -> Iterator[ProblemStore]:
    if request.param == "memory":
        yield MemoryProblemStore(ids=Ids())
        return
    metadata = MetaData()
    tables = problem_tables(metadata, prefix="network_")
    engine = create_engine("sqlite://")
    metadata.create_all(engine)

    @asynccontextmanager
    async def begin() -> AsyncIterator[Any]:
        with Session(engine) as session, session.begin():
            yield _Session(session)

    yield SqlProblemStore(tables, begin, ids=Ids())  # type: ignore[arg-type]
    engine.dispose()


def road_home(**overrides: Any) -> ProblemKind:
    spec: dict[str, Any] = {
        "kind": "RoadHomeMissing",
        "subject": H,
        "requirement": F.road_home.eq(False),
        "message": lambda f: f"host {f.entity.id} has no road home",
        "severity": lambda f: "critical" if f.get("isolated") is True else "warning",
        "action": lambda f: Waiting(for_="handshake"),
        "reads": frozenset({"isolated"}),
    }
    spec.update(overrides)
    return ProblemKind(**spec)


async def history(store: ProblemStore, problem: Problem) -> list[str]:
    found = await store.problem(problem.id)
    assert found is not None
    return [t.to_state for t in found[1]]


def ts(moment: float) -> datetime:
    return datetime.fromtimestamp(moment, UTC)


# ── declaration ────────────────────────────────────────────────────────


def test_kind_is_a_camel_case_reason_and_declared_once() -> None:
    with pytest.raises(ValueError, match="CamelCase"):
        road_home(kind="road-home-missing")
    with pytest.raises(ValueError, match="not both"):
        road_home(requirement=held_for(F.road_home.eq(False), seconds=5), hold=5)
    registry = ProblemRegistry([road_home()])
    with pytest.raises(ValueError, match="twice"):
        registry.add(road_home())
    assert [k.kind for k in registry.for_subject(H)] == ["RoadHomeMissing"]


def test_actions_round_trip_as_tagged_json() -> None:
    import msgspec

    from service_toolkit.problems import Action

    for action in (Remedy("rehand", ts(5)), Waiting(for_="handshake"), NeedsOperator("hosts.rekey", "Re-key"), Nothing()):
        data = msgspec.to_builtins(action)
        assert "type" in data
        assert msgspec.convert(data, Action) == action  # type: ignore[arg-type]
    assert msgspec.to_builtins(Waiting(for_="handshake"))["for"] == "handshake"


# ── store ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_redelivered_transition_writes_nothing(store: ProblemStore) -> None:
    details = ProblemDetails("warning", "m", Nothing(), {})
    first = await store.open("RoadHomeMissing", H, "a", details, ts(1), "t:1:enter")
    again = await store.open("RoadHomeMissing", H, "a", details, ts(2), "t:1:enter")
    assert first is not None and again is not None and again.id == first.id
    assert await store.resolve("RoadHomeMissing", "a", ts(3), "t:1:exit") is not None
    assert await store.resolve("RoadHomeMissing", "a", ts(4), "t:1:exit") is None
    # A stale enter after its problem resolved does not reopen it.
    assert await store.open("RoadHomeMissing", H, "a", details, ts(5), "t:1:enter") is None
    assert await history(store, first) == ["open", "resolved"]


@pytest.mark.asyncio
async def test_queries_for_the_panel(store: ProblemStore) -> None:
    def d(severity: Any) -> ProblemDetails:
        return ProblemDetails(severity, "m", Nothing(), {})

    await store.open("RoadHomeMissing", H, "a", d("warning"), ts(10), "1")
    await store.open("RoadHomeMissing", H, "b", d("critical"), ts(20), "2")
    await store.open("EdgeDown", "edge", "a-b", d("info"), ts(5), "3")
    await store.resolve("RoadHomeMissing", "a", ts(30), "4")

    assert [p.subject_id for p in await store.open_problems()] == ["b", "a-b"]
    assert [p.subject_id for p in await store.open_problems(subject_kind=H)] == ["b"]
    assert [p.kind for p in await store.open_problems(severity=["info"])] == ["EdgeDown"]
    assert await store.open_problems(kind="RoadHomeMissing", subject_id="a") == []

    events = await store.recent(limit=3)
    assert [(e.transition.transition_id, e.subject_id) for e in events] == [("4", "a"), ("2", "b"), ("1", "a")]
    assert [e.transition.transition_id for e in await store.recent(since=ts(15))] == ["4", "2"]


@pytest.mark.asyncio
async def test_compact_drops_old_history_of_resolved_problems(store: ProblemStore) -> None:
    d = ProblemDetails("warning", "m", Nothing(), {})
    now = ts(100 * 86400)
    old = await store.open("RoadHomeMissing", H, "a", d, now - timedelta(days=40), "a1")
    await store.resolve("RoadHomeMissing", "a", now - timedelta(days=35), "a2")
    young = await store.open("RoadHomeMissing", H, "b", d, now - timedelta(days=40), "b1")
    await store.resolve("RoadHomeMissing", "b", now - timedelta(days=1), "b2")
    still = await store.open("RoadHomeMissing", H, "c", d, now - timedelta(days=40), "c1")
    assert old and young and still

    assert await store.compact(before=now - timedelta(days=30)) == 2
    assert await history(store, old) == []
    found = await store.problem(old.id)
    assert found is not None and found[0].resolved_at is not None
    # Resolved recently, or still open: the history stays.
    assert await history(store, young) == ["open", "resolved"]
    assert await history(store, still) == ["open"]


def test_tables_take_the_owners_prefix_and_compile_for_postgres() -> None:
    metadata = MetaData()
    tables = problem_tables(metadata, prefix="fleet_")
    assert {t.name for t in metadata.sorted_tables} == {"fleet_problems", "fleet_problem_transitions"}
    dialect = postgresql.dialect()  # type: ignore[no-untyped-call]
    assert "JSONB" in str(CreateTable(tables.problems).compile(dialect=dialect))
    assert "UNIQUE (transition_id)" in str(CreateTable(tables.transitions).compile(dialect=dialect))
    [open_index] = [i for i in tables.problems.indexes if i.unique]
    assert "WHERE resolved_at IS NULL" in str(CreateIndex(open_index).compile(dialect=dialect))
