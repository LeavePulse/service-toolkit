# `service_toolkit.problems`

A fleet problem used to exist only as a log line. Here it is an entity with
history: what is wrong, with which subject, since when, on what evidence, and
what the system is doing about it. It is built on `service_toolkit.reactive`:
a problem is a reactive requirement that holds.

## Model

A **problem** is one occurrence of a declared kind on one subject. Field names
follow Kubernetes conditions:

| field | meaning |
| --- | --- |
| `kind` | the machine reason, CamelCase and stable (`RoadHomeMissing`). Panels filter on it. |
| `subject_kind`, `subject_id` | what it is about (`host` / `vps-1`, `edge` / `a-b`) |
| `severity` | `info`, `warning` or `critical` |
| `message` | the sentence a person reads |
| `action` | what the system is doing: `Remedy(kind, at)`, `Waiting(for_, until)`, `NeedsOperator(action_id, label)` or `Nothing()`; stored as JSON tagged by `type` |
| `evidence` | a small JSON object, by default every fact the requirement read |
| `first_seen` | when it opened |
| `last_transition` | when it last **changed** (opened, updated, resolved) |
| `last_probe` | when it was last **checked**; a problem checked a minute ago and unchanged for three days shows both |
| `resolved_at` | null while open |
| `observed_generation` | the subject's desired-state generation that was looked at, read from the `generation` fact; lower than the subject's current generation means "not seen your change yet" |

The **transition log** is append-only: `problem_id`, `at`, `from_state`,
`to_state` (`open`, `updated`, `resolved`), `changes` (the fields that changed
and their new values; all of them on open) and `transition_id`, unique, so a
re-delivered transition writes nothing.

## Declaring a kind

Kinds are values, declared beside the code that knows the domain and listed in
one registry:

```python
from service_toolkit.problems import NeedsOperator, ProblemKind, ProblemRegistry, Waiting
from service_toolkit.reactive import Fields

F = Fields()

PROBLEMS = ProblemRegistry([
    ProblemKind(
        kind="RoadHomeMissing",
        subject="host",
        requirement=F.road_home.eq(False),          # TRUE while the problem exists
        message=lambda f: f"{f.entity.id} has no road back to the control plane",
        severity=lambda f: "critical" if f.get("isolated") is True else "warning",
        action=lambda f: Waiting(for_="handshake"),
        hold=30,                                    # held_for: must hold 30 s before it opens
        grace=5,                                    # a break shorter than this does not restart the hold
        reads=frozenset({"isolated"}),              # facts read beyond the requirement
    ),
])
```

`evidence(facts, explanation)` may be given to choose what is kept; the
explanation is the requirement's evaluated tree. `hold` may be a callable read
at every evaluation. `refresh=N` re-checks an open problem every N seconds even
when no facts arrive. A requirement may already be a `held_for` or `latch`, in
which case `hold` is not given.

## Wiring

```python
reactor = Reactor(store=RedisStore(redis, "network:reactive"),
                  timers=RedisTimers(redis, "network:reactive"),
                  is_leader=lease.is_leader, resync=300)
hosts = SnapshotFeed(reactor, "host")                       # provider for "host"
tables = problem_tables(Base.metadata, prefix="network_")   # network_problems, network_problem_transitions
store = SqlProblemStore(tables, async_sessionmaker(engine).begin)
install(reactor, PROBLEMS, store)


async def problems_reconciled(kind: str, seen: frozenset[EntityRef]) -> None:
    await reconcile(reactor, PROBLEMS, store)


reactor.on_reconciled("host", problems_reconciled)
await reactor.start()                                       # timers, resync, reconcile on leader gain
```

`install` registers two rules per kind: `problem:<Kind>` (an edge rule with
`initial="fire"`, so a problem that already exists when the kind ships still
opens; enter opens, exit resolves) and `problem:<Kind>:details` (a converge
rule that keeps an open problem's details current and stamps `last_probe`).

Several replicas: Redis store and timers with a per-service prefix, and
`is_leader` from the toolkit's `LeaderLease`. Only the leader writes.
`MemoryStore` and `MemoryProblemStore` are for tests.

On a module reload use `uninstall(reactor, PROBLEMS)` and `install` again. It
calls `Reactor.remove`; `retire` would resolve every open problem.

## How a pass feeds it

A reconcile pass already holds the state of every subject. It does not wire
events; it hands the whole view over once per pass and then reconciles the
store:

```python
async def pass_once() -> None:
    view = await read_fleet()
    await hosts.push(
        {h.id: {"road_home": h.road_home, "isolated": h.isolated, "generation": h.generation}
         for h in view.hosts},
        removed=view.decommissioned_ids,
    )
    await reconcile(reactor, PROBLEMS, store)
```

Rules for what a pass sends:

- **Emit explicit facts.** A detector that finds a host unreachable sends
  `reachable=False`. A host missing from a pass is UNKNOWN: nothing opens or
  resolves, and a running `hold` pauses rather than counting the gap.
- **Removal is explicit.** Only an id in `removed` (decommissioned, deleted)
  withdraws the subject, which resolves its problems: the feed answers `GONE`
  for it. A subject that disappears without being removed has no facts, which
  reactive reads as UNKNOWN, so its problems stay as they are.
- **Reconcile after the pass.** Edge delivery is at least once, and reactive's
  store can be lost along with an exit. `reconcile` compares each rule's
  recorded state with the store, opens what holds but is missing and resolves
  what no longer holds. A rule with no decision yet, or an effect still being
  retried, is left alone. Each subject is checked under `reactor.locked`, so
  it never races the engine; the facts to phrase a new problem come from
  `reactor.provider(subject)`. The `on_reconciled` hook in the wiring above
  runs the same check after reactive's own reconciles (start, leadership
  gained, resync).

## Rollout

`ProblemKind(mode="shadow")` evaluates and logs transitions
(`reactive_transition ... acting=False`) and writes no problems, and
`reconcile` skips it. Run a kind in shadow beside the detector it replaces,
compare, then switch it to `live`. The reactor's `enabled` switch turns a
rule off at runtime the same way.

## Retention

`store.compact(before=now - timedelta(days=N))` drops transitions older than
the cutoff of problems resolved before it. The problem row stays: it still
says what was wrong and from when until when. Open problems and recently
resolved ones keep their whole history.

## Tests

`tests/test_problems*.py` run every store test against the memory store and
against `SqlProblemStore` on SQLite (through a sync session, since the repo has
no async SQLite driver), and check the DDL compiles for Postgres. Postgres
itself (JSONB, the partial unique index, `FOR UPDATE`) is not exercised here.
