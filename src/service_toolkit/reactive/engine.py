"""The engine: which rules a change wakes, what they decide, what runs.

Semantics, in the order they bite:

* Only the leader evaluates, stores and acts. A follower ignores changes;
  becoming leader triggers a full reconcile.
* Changes to one entity are handled one at a time; entities run in parallel.
* An older facts version than the one last applied is dropped.
* UNKNOWN changes nothing: no exit, no converge toward absent, and the clock of
  a ``held_for`` is paused, not advanced.
* The first evaluation of a rule for an entity is silent: it records the state
  and fires nothing (unless the rule says ``initial="fire"``), so deploying a
  rule or losing the store does not replay enters.
* In one change, converge rules run before edge rules, so an enter handler can
  rely on the access it announces.
* An edge effect is written down as pending before it runs and cleared after;
  a failure is retried with the same transition id and a growing delay.
* ``mode="shadow"`` and the ``enabled`` switch evaluate and record but act on
  nothing, for running a rule beside the code it replaces.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Awaitable, Callable, Iterable
from typing import Any, Literal, Protocol
from weakref import WeakValueDictionary

from service_toolkit.time import unix_now

from .expr import Expr, Explanation, HeldFor, Latch, Requirement, Truth
from .facts import UNKNOWN, EntityRef, Facts
from .rules import ConvergeRule, EdgeRule, Mode, Transition
from .store import PendingEffect, Record, Timers, TransitionStore

__all__ = ["Provider", "Reactor"]

log = logging.getLogger("service_toolkit.reactive")

_random = secrets.SystemRandom()

# Retry delays for a failed effect, in seconds, then the last one repeats.
RETRY_DELAYS = (5.0, 30.0, 120.0, 600.0)


class Provider(Protocol):
    """Where the facts of one kind of entity come from."""

    async def facts(self, entity: EntityRef) -> Facts | None:
        """The current facts, or None when the entity no longer exists."""

    async def entities(self) -> list[EntityRef]:
        """Every entity of this kind, for a reconcile."""

    def ready(self) -> bool:
        """The provider's view is complete. Reconcile waits for it."""

    def dependents(self, entity: EntityRef, fields: frozenset[str]) -> Iterable[tuple[EntityRef, frozenset[str]]]:
        """Entities of other kinds whose joined facts these fields feed, with the fields of theirs that change."""


Rule = ConvergeRule | EdgeRule


def _key(rule: str, entity: EntityRef) -> str:
    return f"{rule}|{entity}"


def _split(key: str) -> tuple[str, EntityRef]:
    rule, _, ref = key.partition("|")
    kind, _, ident = ref.partition(":")
    return rule, EntityRef(kind, ident)


class Reactor:
    def __init__(
        self,
        *,
        store: TransitionStore,
        timers: Timers,
        is_leader: Callable[[], bool] = lambda: True,
        enabled: Callable[[str], bool] = lambda rule: True,
        clock: Callable[[], float] = unix_now,
        concurrency: int = 8,
        reconcile_rate: float = 50.0,
        tick: float = 1.0,
        resync: float | None = None,
        jitter: float = 0.1,
    ) -> None:
        self.store = store
        self.timers = timers
        self.is_leader = is_leader
        self.enabled = enabled
        self.clock = clock
        self.reconcile_rate = reconcile_rate
        self.tick = tick
        self.resync = resync
        self.jitter = jitter
        self._providers: dict[str, Provider] = {}
        self._rules: dict[str, Rule] = {}
        self._index: dict[str, dict[str, list[str]]] = {}
        self._owners: dict[str, str] = {}
        self._locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
        self._slots = asyncio.Semaphore(concurrency)
        self._tasks: set[asyncio.Task[Any]] = set()
        self._loop: asyncio.Task[None] | None = None
        self._was_leader = False

    # ── registration ─────────────────────────────────────────────────────

    def provide(self, kind: str, provider: Provider) -> None:
        self._providers[kind] = provider

    def add(self, rule: Rule) -> Rule:
        if rule.name in self._rules:
            raise ValueError(f"rule {rule.name!r} is registered twice")
        self._rules[rule.name] = rule
        by_field = self._index.setdefault(rule.kind, {})
        for name in rule.fields():
            by_field.setdefault(name, []).append(rule.name)
        return rule

    def on_enter(
        self, requirement: Requirement, *, kind: str, name: str | None = None, settle: float = 0.0,
        initial: Literal["silent", "fire"] = "silent", mode: Mode = "live", repeat: float = 0.0,
    ) -> Callable[[Callable[[EntityRef, Facts, Transition], Awaitable[None]]], Callable[[EntityRef, Facts, Transition], Awaitable[None]]]:
        def wrap(fn: Callable[[EntityRef, Facts, Transition], Awaitable[None]]) -> Callable[[EntityRef, Facts, Transition], Awaitable[None]]:
            self.add(EdgeRule(name or fn.__name__, kind, requirement, on_enter=fn, settle=settle, initial=initial, mode=mode, repeat=repeat))
            return fn

        return wrap

    def on_exit(
        self, requirement: Requirement, *, kind: str, name: str | None = None, settle: float = 0.0, mode: Mode = "live",
    ) -> Callable[[Callable[[EntityRef, Facts, Transition], Awaitable[None]]], Callable[[EntityRef, Facts, Transition], Awaitable[None]]]:
        def wrap(fn: Callable[[EntityRef, Facts, Transition], Awaitable[None]]) -> Callable[[EntityRef, Facts, Transition], Awaitable[None]]:
            self.add(EdgeRule(name or fn.__name__, kind, requirement, on_exit=fn, settle=settle, mode=mode))
            return fn

        return wrap

    def converge(
        self,
        desired: Expr | Callable[[Facts], Any],
        *,
        kind: str,
        observe: Callable[[EntityRef], Awaitable[Any]],
        name: str | None = None,
        reads: Iterable[str] = (),
        withdraw: Callable[[EntityRef], Awaitable[None]] | None = None,
        owns: Callable[[EntityRef], Iterable[str]] | None = None,
        verify: bool = True,
        mode: Mode = "live",
    ) -> Callable[[Callable[[EntityRef, Any], Awaitable[None]]], Callable[[EntityRef, Any], Awaitable[None]]]:
        """``desired`` is a requirement (the desired value is whether it holds) or a function of the facts."""
        if isinstance(desired, Expr):
            expr = desired
            want: Callable[[Facts], Any] = expr.evaluate
            fields = expr.fields() | frozenset(reads)
        else:
            want = desired
            fields = frozenset(reads)
            if not fields:
                raise ValueError("a converge with a function for `desired` must name the fields it reads")

        def wrap(fn: Callable[[EntityRef, Any], Awaitable[None]]) -> Callable[[EntityRef, Any], Awaitable[None]]:
            self.add(ConvergeRule(
                name or fn.__name__, kind, want, observe, fn, fields,
                withdraw=withdraw, owns=owns, verify=verify, mode=mode,
            ))
            return fn

        return wrap

    def _acting(self, rule: Rule) -> bool:
        return rule.mode == "live" and self.enabled(rule.name)

    def _rules_for(self, kind: str, fields: Iterable[str] | None) -> list[Rule]:
        if fields is None:
            names = [n for n, r in self._rules.items() if r.kind == kind]
        else:
            by_field = self._index.get(kind, {})
            seen: dict[str, None] = {}
            for f in fields:
                for n in by_field.get(f, ()):
                    seen[n] = None
            names = list(seen)
        rules = [self._rules[n] for n in names]
        # Converge first: an enter handler may announce what converge grants.
        return sorted(rules, key=lambda r: 0 if isinstance(r, ConvergeRule) else 1)

    # ── evaluation ───────────────────────────────────────────────────────

    def _truth(self, requirement: Requirement, facts: Facts, record: Record, now: float) -> tuple[Truth, float | None]:
        """Whether the requirement holds, and when to look again (for time)."""
        if isinstance(requirement, Latch):
            if record.holds is True:
                leave = requirement.exit.evaluate(facts)
                return (UNKNOWN if leave is UNKNOWN else not leave), None
            return requirement.enter.evaluate(facts), None
        if isinstance(requirement, HeldFor):
            inner = requirement.inner.evaluate(facts)
            if inner is UNKNOWN:
                # Missing data pauses the clock: the gap is neither counted
                # as holding nor as a break.
                if record.run_since is not None and record.paused_at is None:
                    record.paused_at = now
                return UNKNOWN, None
            if record.paused_at is not None and record.run_since is not None:
                record.run_since += now - record.paused_at
            record.paused_at = None
            if inner is False:
                record.run_since = None
                return False, None
            if record.run_since is None:
                record.run_since = now
            due = record.run_since + requirement.seconds
            return (True, None) if now >= due else (False, due)
        return requirement.evaluate(facts), None

    async def _edge(self, rule: EdgeRule, facts: Facts) -> None:
        entity = facts.entity
        now = self.clock()
        stored = await self.store.get(rule.name, str(entity))
        if stored is not None and facts.version is not None and stored.version is not None and facts.version < stored.version:
            log.debug("reactive_stale_facts", extra={"rule": rule.name, "entity": str(entity)})
            return
        record = stored or Record()
        truth, due = self._truth(rule.requirement, facts, record, now)
        if facts.version is not None:
            record.version = facts.version
        if due is not None:
            await self.timers.schedule(_key(rule.name, entity), self._jittered(due, now))

        if truth is UNKNOWN:
            await self.store.put(rule.name, str(entity), record)
            return

        if stored is None or stored.holds is None:
            record.holds = bool(truth)
            record.since = now
            await self.store.put(rule.name, str(entity), record)
            if truth and rule.initial == "fire":
                await self._commit(rule, facts, record, True, now)
            return

        if truth == record.holds:
            record.candidate = None
            record.candidate_due = None
            if truth and rule.repeat > 0 and record.next_repeat is not None and now >= record.next_repeat:
                await self._repeat(rule, facts, record, now)
                return
            await self.store.put(rule.name, str(entity), record)
            return

        if rule.settle > 0:
            if record.candidate is not truth or record.candidate_due is None:
                record.candidate = bool(truth)
                record.candidate_due = now + rule.settle
                await self.store.put(rule.name, str(entity), record)
                await self.timers.schedule(_key(rule.name, entity), record.candidate_due)
                return
            if now < record.candidate_due:
                await self.store.put(rule.name, str(entity), record)
                return

        await self._commit(rule, facts, record, bool(truth), now)

    async def _commit(self, rule: EdgeRule, facts: Facts, record: Record, holds: bool, now: float) -> None:
        entity = facts.entity
        record.holds = holds
        record.since = now
        record.candidate = None
        record.candidate_due = None
        kind: Literal["enter", "exit"] = "enter" if holds else "exit"
        if holds:
            record.epoch += 1
            record.repeats = 0
            record.next_repeat = now + rule.repeat if rule.repeat > 0 else None
            if record.next_repeat is not None:
                await self.timers.schedule(_key(rule.name, entity), record.next_repeat)
        else:
            record.next_repeat = None
        handler = rule.on_enter if holds else rule.on_exit
        if handler is None:
            await self.store.put(rule.name, str(entity), record)
            return
        transition = Transition(f"{rule.name}:{entity}:{record.epoch}:{kind}", rule.name, entity, kind, record.epoch, now)
        # Written down before it runs: a crash in between retries it with the
        # same id instead of forgetting it.
        record.pending = (*record.pending, PendingEffect(transition.id, kind))
        await self.store.put(rule.name, str(entity), record)
        log.info("reactive_transition", extra={"rule": rule.name, "entity": str(entity), "transition": kind, "id": transition.id, "acting": self._acting(rule)})
        await self._run_pending(rule, facts, record)

    async def _repeat(self, rule: EdgeRule, facts: Facts, record: Record, now: float) -> None:
        """The heartbeat of a held requirement: on_enter again, with its own id."""
        entity = facts.entity
        record.repeats += 1
        record.next_repeat = now + rule.repeat
        effect_id = f"{rule.name}:{entity}:{record.epoch}:enter:r{record.repeats}"
        record.pending = (*record.pending, PendingEffect(effect_id, "enter"))
        await self.store.put(rule.name, str(entity), record)
        await self.timers.schedule(_key(rule.name, entity), record.next_repeat)
        await self._run_pending(rule, facts, record)

    async def _run_pending(self, rule: EdgeRule, facts: Facts, record: Record) -> None:
        entity = facts.entity
        left: list[PendingEffect] = []
        retry_at: float | None = None
        for effect in record.pending:
            handler = rule.on_enter if effect.kind == "enter" else rule.on_exit
            if handler is None or not self._acting(rule):
                continue
            epoch = record.epoch
            transition = Transition(effect.id, rule.name, entity, "enter" if effect.kind == "enter" else "exit", epoch, self.clock())
            try:
                await handler(entity, facts, transition)
            except Exception:
                log.exception("reactive_effect_failed", extra={"rule": rule.name, "entity": str(entity), "id": effect.id, "attempts": effect.attempts + 1})
                left.append(PendingEffect(effect.id, effect.kind, effect.attempts + 1))
                delay = RETRY_DELAYS[min(effect.attempts, len(RETRY_DELAYS) - 1)]
                retry_at = min(retry_at or float("inf"), self.clock() + delay)
        record.pending = tuple(left)
        await self.store.put(rule.name, str(entity), record)
        if retry_at is not None:
            await self.timers.schedule(_key(rule.name, entity), retry_at)

    async def _converge(self, rule: ConvergeRule, facts: Facts) -> None:
        entity = facts.entity
        stored = await self.store.get(rule.name, str(entity))
        if stored is not None and facts.version is not None and stored.version is not None and facts.version < stored.version:
            return
        record = stored or Record()
        if facts.version is not None:
            record.version = facts.version
        desired = rule.desired(facts)
        if desired is UNKNOWN:
            await self.store.put(rule.name, str(entity), record)
            return
        if not self._acting(rule):
            log.info("reactive_converge_shadow", extra={"rule": rule.name, "entity": str(entity), "desired": repr(desired)})
            await self.store.put(rule.name, str(entity), record)
            return
        for key in (rule.owns(entity) if rule.owns else ()):
            owner = self._owners.setdefault(key, rule.name)
            if owner != rule.name:
                log.error("reactive_owner_conflict", extra={"rule": rule.name, "owner": owner, "key": key})
                return
        observed = await rule.observe(entity)
        # Compared with what the target says it is, never with what was sent
        # last: a target that restarted or was edited by hand is put right.
        if observed is not UNKNOWN and observed == desired:
            record.attempts = 0
            await self.store.put(rule.name, str(entity), record)
            return
        failed = False
        try:
            await rule.apply(entity, desired)
            if rule.verify:
                after = await rule.observe(entity)
                if after is not UNKNOWN and after != desired:
                    failed = True
                    log.error("reactive_read_back_mismatch", extra={"rule": rule.name, "entity": str(entity), "desired": repr(desired), "observed": repr(after)})
        except Exception:
            failed = True
            log.exception("reactive_converge_failed", extra={"rule": rule.name, "entity": str(entity)})
        if failed:
            delay = RETRY_DELAYS[min(record.attempts, len(RETRY_DELAYS) - 1)]
            record.attempts += 1
            await self.timers.schedule(_key(rule.name, entity), self.clock() + delay)
        else:
            record.attempts = 0
        await self.store.put(rule.name, str(entity), record)

    def _jittered(self, due: float, now: float) -> float:
        if self.jitter <= 0 or due <= now:
            return due
        return due + _random.uniform(0, (due - now) * self.jitter)

    # ── driving ──────────────────────────────────────────────────────────

    def _lock(self, entity: EntityRef) -> asyncio.Lock:
        name = str(entity)
        lock = self._locks.get(name)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[name] = lock
        return lock

    async def changed(self, facts: Facts, fields: Iterable[str] | None = None) -> None:
        """Facts of one entity changed. ``fields`` narrows which rules wake; None wakes all of its kind."""
        if not self.is_leader():
            return
        wanted = frozenset(fields) if fields is not None else None
        async with self._lock(facts.entity):
            for rule in self._rules_for(facts.entity.kind, wanted):
                await self._evaluate(rule, facts)
        provider = self._providers.get(facts.entity.kind)
        if provider is not None and wanted is not None:
            for other, their_fields in provider.dependents(facts.entity, wanted):
                await self.refresh(other, their_fields)

    def submit(self, facts: Facts, fields: Iterable[str] | None = None) -> None:
        """``changed`` without waiting, for an event handler that must return quickly."""
        self._spawn(self.changed(facts, fields))

    async def refresh(self, entity: EntityRef, fields: Iterable[str] | None = None) -> None:
        """Re-read the entity from its provider and evaluate."""
        provider = self._providers.get(entity.kind)
        if provider is None or not self.is_leader():
            return
        facts = await provider.facts(entity)
        if facts is None:
            await self.gone(entity)
            return
        await self.changed(facts, fields)

    async def gone(self, entity: EntityRef) -> None:
        """The entity left every rule's scope: withdraw converge, exit what was entered."""
        if not self.is_leader():
            return
        async with self._lock(entity):
            for rule in self._rules_for(entity.kind, None):
                await self._withdraw(rule, entity)

    async def retire(self, name: str) -> None:
        """Remove a rule and withdraw everything it holds, so nothing it granted outlives it."""
        rule = self._rules.get(name)
        if rule is None:
            return
        for ident in await self.store.entities(name):
            kind, _, rest = ident.partition(":")
            entity = EntityRef(kind, rest)
            async with self._lock(entity):
                await self._withdraw(rule, entity)
        del self._rules[name]
        for names in self._index.get(rule.kind, {}).values():
            if name in names:
                names.remove(name)
        self._owners = {k: v for k, v in self._owners.items() if v != name}

    async def _withdraw(self, rule: Rule, entity: EntityRef) -> None:
        record = await self.store.get(rule.name, str(entity))
        if isinstance(rule, ConvergeRule):
            if rule.withdraw is not None and self._acting(rule):
                try:
                    await rule.withdraw(entity)
                except Exception:
                    log.exception("reactive_withdraw_failed", extra={"rule": rule.name, "entity": str(entity)})
                    return
        elif record is not None:
            if record.holds:
                await self._commit(rule, Facts(entity, {}), record, False, self.clock())
            # An exit still being retried keeps its record until it is done.
            if record.pending:
                return
        await self.store.delete(rule.name, str(entity))
        await self.timers.cancel(_key(rule.name, entity))

    async def _evaluate(self, rule: Rule, facts: Facts) -> None:
        try:
            if isinstance(rule, ConvergeRule):
                await self._converge(rule, facts)
            else:
                await self._edge(rule, facts)
        except Exception:
            log.exception("reactive_evaluate_failed", extra={"rule": rule.name, "entity": str(facts.entity)})

    async def run_timers(self) -> None:
        """Handle every due timer: a held_for that may now hold, a settle, a retry."""
        if not self.is_leader():
            return
        for key in await self.timers.take_due(self.clock()):
            name, entity = _split(key)
            rule = self._rules.get(name)
            provider = self._providers.get(entity.kind)
            if rule is None or provider is None:
                continue
            facts = await provider.facts(entity)
            async with self._lock(entity):
                record = await self.store.get(name, str(entity))
                if isinstance(rule, EdgeRule) and record is not None and record.pending:
                    await self._run_pending(rule, facts or Facts(entity, {}), record)
                if facts is None:
                    await self._withdraw(rule, entity)
                    continue
                await self._evaluate(rule, facts)

    async def reconcile(self, kind: str | None = None) -> None:
        """Evaluate every rule for every entity, paced so a large scope does not burst a platform's limits."""
        if not self.is_leader():
            return
        kinds = [kind] if kind else list(self._providers)
        for each in kinds:
            provider = self._providers[each]
            entities = await provider.entities()
            log.info("reactive_reconcile", extra={"kind": each, "entities": len(entities)})
            pause = 1.0 / self.reconcile_rate if self.reconcile_rate > 0 else 0.0
            pending: list[asyncio.Task[None]] = []
            for entity in entities:
                if not self.is_leader():
                    break
                pending.append(asyncio.create_task(self._reconcile_one(entity)))
                if pause:
                    await asyncio.sleep(pause)
            await asyncio.gather(*pending, return_exceptions=True)
            # Entities the store remembers but the provider no longer lists have left.
            listed = {str(e) for e in entities}
            for name, rule in list(self._rules.items()):
                if rule.kind != each:
                    continue
                for ident in await self.store.entities(name):
                    if ident not in listed:
                        k, _, rest = ident.partition(":")
                        await self.gone(EntityRef(k, rest))

    async def _reconcile_one(self, entity: EntityRef) -> None:
        async with self._slots:
            await self.refresh(entity)

    def _spawn(self, coro: Awaitable[None]) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def explain(self, name: str, entity: EntityRef) -> Explanation | None:
        """Why a rule holds or not for an entity, from fresh facts."""
        rule = self._rules.get(name)
        provider = self._providers.get(entity.kind)
        if rule is None or provider is None:
            return None
        facts = await provider.facts(entity)
        if facts is None:
            return Explanation(f"{name}: entity is gone", UNKNOWN)
        if isinstance(rule, ConvergeRule):
            return Explanation(f"{name}: desired", True, {"desired": rule.desired(facts)})
        requirement = rule.requirement
        record = await self.store.get(name, str(entity)) or Record()
        if isinstance(requirement, HeldFor):
            inner = requirement.inner.explain(facts)
            truth, _ = self._truth(requirement, facts, record, self.clock())
            return Explanation(f"held for {requirement.seconds:g}s", truth, {"run_since": record.run_since}, [inner])
        if isinstance(requirement, Latch):
            truth, _ = self._truth(requirement, facts, record, self.clock())
            return Explanation("latch", truth, {"held": record.holds}, [requirement.enter.explain(facts), requirement.exit.explain(facts)])
        return requirement.explain(facts)

    # ── lifecycle ────────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._loop is None:
            self._loop = asyncio.create_task(self._run())

    async def stop(self) -> None:
        loop, self._loop = self._loop, None
        if loop is not None:
            loop.cancel()
            await asyncio.gather(loop, return_exceptions=True)
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    def _ready(self) -> bool:
        return all(p.ready() for p in self._providers.values())

    async def _run(self) -> None:
        next_resync: float | None = None
        while True:
            try:
                leading = self.is_leader()
                if leading and self._ready():
                    if not self._was_leader:
                        # Gained leadership, or the first start: events may have
                        # been missed, so the state is read in full.
                        self._was_leader = True
                        await self.reconcile()
                        next_resync = self._next_resync()
                    elif next_resync is not None and self.clock() >= next_resync:
                        await self.reconcile()
                        next_resync = self._next_resync()
                    await self.run_timers()
                elif not leading:
                    self._was_leader = False
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("reactive_loop_failed")
            await asyncio.sleep(self.tick)

    def _next_resync(self) -> float | None:
        if not self.resync:
            return None
        return self.clock() + self.resync * (1 + _random.uniform(0, self.jitter))
