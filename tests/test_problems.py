from __future__ import annotations

import re
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
    SnapshotFeed,
    Waiting,
    install,
    reconcile,
    uninstall,
)
from service_toolkit.problems.sql import SqlProblemStore, problem_tables
from service_toolkit.reactive import Fields, MemoryStore, MemoryTimers, Reactor, held_for

F = Fields()
H = "host"


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


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


def world(store: ProblemStore, *kinds: ProblemKind) -> tuple[Reactor, SnapshotFeed, Clock]:
    clock = Clock()
    reactor = Reactor(store=MemoryStore(), timers=MemoryTimers(), clock=clock, jitter=0, reconcile_rate=0)
    feed = SnapshotFeed(reactor, H)
    install(reactor, kinds, store)
    return reactor, feed, clock


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


def test_the_vocabulary_tuples_are_the_literals_and_the_action_tags() -> None:
    from typing import get_args

    from service_toolkit.problems import ACTION_TYPES, SEVERITIES, Action, ActionType, Severity

    assert SEVERITIES == ("info", "warning", "critical") == get_args(Severity)
    assert ACTION_TYPES == get_args(ActionType)
    assert {variant.__struct_config__.tag for variant in get_args(Action)} == set(ACTION_TYPES)


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


# ── lifecycle ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_opens_after_hold_updates_and_resolves(store: ProblemStore) -> None:
    _, feed, clock = world(store, road_home(hold=30))

    await feed.push({"a": {"road_home": False, "isolated": False, "generation": 7}})
    assert await store.open_problems() == []
    clock.now += 31
    await feed.push({"a": {"road_home": False, "isolated": False, "generation": 7}})
    [problem] = await store.open_problems()
    assert (problem.kind, problem.subject_kind, problem.subject_id) == ("RoadHomeMissing", H, "a")
    assert problem.severity == "warning"
    assert problem.message == "host a has no road home"
    assert problem.action == Waiting(for_="handshake")
    assert problem.evidence == {"road_home": False}
    assert problem.observed_generation == 7
    assert problem.first_seen == ts(clock.now)

    # A change while open is an update; the same facts again only move last_probe.
    clock.now += 10
    await feed.push({"a": {"road_home": False, "isolated": True, "generation": 7}})
    clock.now += 10
    await feed.push({"a": {"road_home": False, "isolated": True, "generation": 7}})
    [problem] = await store.open_problems()
    assert problem.severity == "critical"
    assert problem.last_transition == ts(clock.now - 10)
    assert problem.last_probe == ts(clock.now)

    clock.now += 5
    await feed.push({"a": {"road_home": True, "isolated": True, "generation": 7}})
    assert await store.open_problems() == []
    found = await store.problem(problem.id)
    assert found is not None
    resolved, rows = found
    assert resolved.resolved_at == ts(clock.now)
    assert [(r.from_state, r.to_state) for r in rows] == [(None, "open"), ("open", "updated"), ("updated", "resolved")]
    assert rows[1].changes == {"severity": "critical"}
    assert re.fullmatch(r"problem:RoadHomeMissing:host:a:[0-9a-f]{8}-1:enter", rows[0].transition_id)


@pytest.mark.asyncio
async def test_a_problem_present_when_the_kind_ships_opens(store: ProblemStore) -> None:
    # Reactive's first evaluation is silent by default; problems must not be.
    _, feed, _ = world(store, road_home())
    await feed.push({"a": {"road_home": False}})
    assert [p.subject_id for p in await store.open_problems()] == ["a"]


class CrashAfterOpen:
    """Writes the open, then fails, as a crash before reactive records success would."""

    def __init__(self, inner: ProblemStore) -> None:
        self.inner = inner
        self.crashes = 1

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    async def open(self, *args: Any) -> Problem | None:
        result = await self.inner.open(*args)
        if self.crashes:
            self.crashes -= 1
            raise RuntimeError("lost after commit")
        return result


@pytest.mark.asyncio
async def test_a_retried_enter_after_a_committed_open_is_one_open(store: ProblemStore) -> None:
    flaky = CrashAfterOpen(store)
    reactor, feed, clock = world(flaky, road_home())  # type: ignore[arg-type]
    await feed.push({"a": {"road_home": False}})
    assert flaky.crashes == 0
    clock.now += 600
    await reactor.run_timers()
    [problem] = await store.open_problems()
    assert await history(store, problem) == ["open"]


@pytest.mark.asyncio
async def test_a_missing_entity_is_unknown_and_a_removed_one_resolves(store: ProblemStore) -> None:
    _, feed, _ = world(store, road_home())
    await feed.push({"a": {"road_home": False}, "b": {"road_home": False}})
    assert len(await store.open_problems()) == 2

    # Not seen this pass: nothing is known, nothing resolves.
    await feed.push({"b": {"road_home": False}})
    assert len(await store.open_problems()) == 2

    # Decommissioned: said explicitly, and only then resolved.
    await feed.push({"b": {"road_home": False}}, removed=["a"])
    assert [p.subject_id for p in await store.open_problems()] == ["b"]


@pytest.mark.asyncio
async def test_held_for_pauses_while_the_entity_is_unseen(store: ProblemStore) -> None:
    _, feed, clock = world(store, road_home(hold=30))
    await feed.push({"a": {"road_home": False}})
    clock.now += 20
    await feed.push({})
    clock.now += 100
    await feed.push({})
    assert await store.open_problems() == []
    # The unseen gap did not count: 20s held so far, 10 more to go.
    await feed.push({"a": {"road_home": False}})
    clock.now += 9
    await feed.push({"a": {"road_home": False}})
    assert await store.open_problems() == []
    clock.now += 2
    await feed.push({"a": {"road_home": False}})
    assert len(await store.open_problems()) == 1


@pytest.mark.asyncio
async def test_reconcile_closes_what_a_lost_store_forgot(store: ProblemStore) -> None:
    kind = road_home()
    reactor, feed, _ = world(store, kind)
    await feed.push({"a": {"road_home": False}})
    # Reactive's store is lost, and the host heals meanwhile: the first new
    # evaluation records "does not hold" without an exit.
    reactor.store = MemoryStore()
    await feed.push({"a": {"road_home": True}})
    assert len(await store.open_problems()) == 1

    report = await reconcile(reactor, [kind], store)
    assert (report.opened, report.resolved) == (0, 1)
    assert await store.open_problems() == []


@pytest.mark.asyncio
async def test_reconcile_opens_what_holds_but_is_missing(store: ProblemStore) -> None:
    kind = road_home()
    reactor, feed, _ = world(store, kind)
    await feed.push({"a": {"road_home": False}})
    [problem] = await store.open_problems()
    await store.resolve(kind.kind, "a", ts(1), "lost-elsewhere")
    report = await reconcile(reactor, [kind], store)
    assert report.opened == 1
    assert [p.subject_id for p in await store.open_problems()] == ["a"]
    # Nothing left to do: a second reconcile is a no-op.
    report = await reconcile(reactor, [kind], store)
    assert (report.opened, report.resolved) == (0, 0)


@pytest.mark.asyncio
async def test_an_enter_after_a_store_loss_opens_a_new_problem(store: ProblemStore) -> None:
    reactor, feed, _ = world(store, road_home())
    await feed.push({"a": {"road_home": False}})
    await feed.push({"a": {"road_home": True}})
    reactor.store = MemoryStore()
    # Epochs start again at 1; the record's new generation keeps the id fresh.
    await feed.push({"a": {"road_home": False}})
    [problem] = await store.open_problems()
    events = await store.recent()
    assert len({e.transition.problem_id for e in events}) == 2
    assert len({e.transition.transition_id for e in events}) == len(events)


@pytest.mark.asyncio
async def test_shadow_writes_nothing(store: ProblemStore) -> None:
    kind = road_home(mode="shadow")
    reactor, feed, _ = world(store, kind)
    await feed.push({"a": {"road_home": False}})
    assert await store.open_problems() == []
    await reconcile(reactor, [kind], store)
    assert await store.open_problems() == []


@pytest.mark.asyncio
async def test_uninstall_keeps_open_problems(store: ProblemStore) -> None:
    kind = road_home()
    reactor, feed, _ = world(store, kind)
    await feed.push({"a": {"road_home": False}})
    uninstall(reactor, [kind])
    install(reactor, [kind], store)
    await feed.push({"a": {"road_home": False}})
    [problem] = await store.open_problems()
    assert await history(store, problem) == ["open"]


async def open_held_then_restart(store: ProblemStore, *, lose_reactive_store: bool) -> tuple[Reactor, SnapshotFeed, Clock, ProblemKind]:
    """A held problem is open; the service restarts (a new Reactor over the same
    reactive store, or a fresh one), reconciles before its first pass, then passes."""
    kind = road_home(hold=30)
    reactor, feed, clock = world(store, kind)
    await feed.push({"a": {"road_home": False}})
    clock.now += 31
    await feed.push({"a": {"road_home": False}})
    assert len(await store.open_problems()) == 1
    clock.now += 100
    reactive = MemoryStore() if lose_reactive_store else reactor.store
    reactor = Reactor(store=reactive, timers=reactor.timers, clock=clock, jitter=0, reconcile_rate=0)
    feed = SnapshotFeed(reactor, H)
    install(reactor, [kind], store)
    await reactor.reconcile()
    await reconcile(reactor, [kind], store)
    await feed.push({"a": {"road_home": False}})
    await reconcile(reactor, [kind], store)
    return reactor, feed, clock, kind


@pytest.mark.asyncio
async def test_a_restart_keeps_an_open_held_problem(store: ProblemStore) -> None:
    await open_held_then_restart(store, lose_reactive_store=False)
    [problem] = await store.open_problems()
    assert await history(store, problem) == ["open"]
    assert len(await store.recent()) == 1


@pytest.mark.asyncio
async def test_a_restart_without_reactive_state_keeps_an_open_held_problem(store: ProblemStore) -> None:
    # The new reactor starts the hold over; "true but not served yet" is no
    # decision, so the open problem is neither resolved nor opened again.
    reactor, feed, clock, kind = await open_held_then_restart(store, lose_reactive_store=True)
    clock.now += 31
    await feed.push({"a": {"road_home": False}})
    await reconcile(reactor, [kind], store)
    [problem] = await store.open_problems()
    assert await history(store, problem) == ["open"]
    assert len(await store.recent()) == 1
