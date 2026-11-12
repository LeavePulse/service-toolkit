"""Problem kinds as reactive rules, and the store kept in step with them.

Each kind becomes two rules on one ``Reactor``:

* ``problem:<Kind>``, an ``EdgeRule`` over the kind's requirement (wrapped in
  ``held_for`` when the kind has a ``hold``). Its enter opens the problem, its
  exit resolves it. It uses ``initial="fire"``: a problem that already exists
  when a kind ships, or after reactive's store was lost, must still open.
* ``problem:<Kind>:details``, a ``ConvergeRule`` that keeps an open problem's
  message, severity, action, evidence and generation current, and stamps
  ``last_probe`` at every evaluation. An edge fires once per crossing; a
  change while the problem stays open is a state to keep, which is converge's
  job.

Edge delivery is at least once, and reactive's store may be lost (its exits
with it), so the handlers sync to the rule's recorded state instead of
trusting the edge alone, and ``reconcile`` closes the gap from the other side:
it opens what the rule says holds and closes what it says does not.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from service_toolkit.reactive import UNKNOWN, ConvergeRule, EdgeRule, EntityRef, Facts, Reactor, Transition

from .model import ProblemDetails, ProblemKind
from .store import ProblemStore

__all__ = ["ReconcileReport", "install", "reconcile", "uninstall"]

log = logging.getLogger("service_toolkit.problems")


def _at(moment: float) -> datetime:
    return datetime.fromtimestamp(moment, UTC)


async def _unread(entity: EntityRef) -> Any:
    # The store compares details itself; UNKNOWN makes the engine call apply
    # at every evaluation, which is what stamps last_probe.
    return UNKNOWN


def _rules(reactor: Reactor, kind: ProblemKind, store: ProblemStore) -> tuple[EdgeRule, ConvergeRule]:
    async def entered(entity: EntityRef, facts: Facts, t: Transition) -> None:
        record = await reactor.store.get(kind.rule_name, str(entity))
        if record is not None and record.holds is False:
            # A later exit is queued behind this enter; the pair nets to nothing.
            return
        problem = await store.open(kind.kind, entity.kind, entity.id, kind.details(facts), _at(t.at), t.id)
        if problem is not None:
            log.info("problem_open kind=%s subject=%s problem=%s transition=%s", kind.kind, str(entity), problem.id, t.id)

    async def exited(entity: EntityRef, facts: Facts, t: Transition) -> None:
        record = await reactor.store.get(kind.rule_name, str(entity))
        if record is not None and record.holds is True:
            # Entered again since; the newer enter decides.
            return
        if await store.get_open(kind.kind, entity.id) is None:
            return
        problem = await store.resolve(kind.kind, entity.id, _at(t.at), t.id)
        if problem is not None:
            log.info("problem_resolved kind=%s subject=%s problem=%s transition=%s", kind.kind, str(entity), problem.id, t.id)

    async def keep(entity: EntityRef, details: Any) -> None:
        if isinstance(details, ProblemDetails):
            await store.update(kind.kind, entity.id, details, _at(reactor.clock()))

    edge = EdgeRule(
        kind.rule_name,
        kind.subject,
        kind.edge_requirement(),
        on_enter=entered,
        on_exit=exited,
        initial="fire",
        repeat=kind.refresh,
        mode=kind.mode,
    )
    converge = ConvergeRule(
        kind.detail_rule_name,
        kind.subject,
        desired=kind.current,
        observe=_unread,
        apply=keep,
        reads=kind.fields(),
        verify=False,
        mode=kind.mode,
    )
    return edge, converge


def install(reactor: Reactor, kinds: Iterable[ProblemKind], store: ProblemStore) -> list[str]:
    """Register every kind's rules on ``reactor``. Returns the rule names, for ``uninstall``."""
    names: list[str] = []
    for kind in kinds:
        for rule in _rules(reactor, kind, store):
            reactor.add(rule)
            names.append(rule.name)
    return names


def uninstall(reactor: Reactor, kinds: Iterable[ProblemKind]) -> None:
    """Forget the kinds' rules and keep every problem as it is: for a module reload.

    Uses ``Reactor.remove``, never ``retire``: retiring would run every exit and
    resolve every open problem just because the code was reloaded.
    """
    for kind in kinds:
        reactor.remove(kind.rule_name)
        reactor.remove(kind.detail_rule_name)


@dataclass(slots=True)
class ReconcileReport:
    opened: int = 0
    resolved: int = 0


async def reconcile(
    reactor: Reactor,
    kinds: Iterable[ProblemKind],
    store: ProblemStore,
) -> ReconcileReport:
    """Bring the problem store in line with what each rule has recorded.

    Run it after every pass, and hook it to the reactor's own reconciles
    (``Reactor.on_reconciled``): an exit lost with reactive's store, or an
    effect that failed for good, leaves a problem open (or missing) that no
    future edge would fix. A rule with no decision for an entity (no record,
    UNKNOWN so far, or an effect still being retried) is left alone: "not
    known" never resolves anything. Each subject is handled under the
    reactor's lock for it, so the engine cannot cross it halfway.

    A problem that must be opened is phrased from the kind's provider; an
    entity it cannot describe is skipped until it can.
    """
    report = ReconcileReport()
    if not reactor.is_leader():
        return report
    for kind in kinds:
        if kind.mode != "live" or not reactor.enabled(kind.rule_name):
            continue
        prefix = f"{kind.subject}:"
        subjects = {p.subject_id for p in await store.open_problems(kind=kind.kind)}
        subjects.update(e[len(prefix):] for e in await reactor.store.entities(kind.rule_name) if e.startswith(prefix))
        for subject_id in sorted(subjects):
            entity = EntityRef(kind.subject, subject_id)
            async with reactor.locked(entity):
                await _reconcile_one(reactor, kind, store, entity, report)
    return report


async def _reconcile_one(reactor: Reactor, kind: ProblemKind, store: ProblemStore, entity: EntityRef, report: ReconcileReport) -> None:
    record = await reactor.store.get(kind.rule_name, str(entity))
    if record is None or not record.decided or record.pending:
        return
    moment = reactor.clock()
    crossing = f"{kind.rule_name}:{entity}:{record.generation}-{record.epoch}"
    problem = await store.get_open(kind.kind, entity.id)
    if record.holds and problem is None:
        provider = reactor.provider(kind.subject)
        facts = await provider.facts(entity) if provider is not None else None
        if not isinstance(facts, Facts):
            return
        transition_id = f"{crossing}:enter:reconciled@{int(moment * 1000)}"
        if await store.open(kind.kind, entity.kind, entity.id, kind.details(facts), _at(moment), transition_id):
            report.opened += 1
            log.warning("problem_reconciled_open kind=%s subject=%s", kind.kind, str(entity))
    elif not record.holds and problem is not None:
        transition_id = f"{crossing}:exit:reconciled@{int(moment * 1000)}"
        if await store.resolve(kind.kind, entity.id, _at(moment), transition_id):
            report.resolved += 1
            log.warning("problem_reconciled_resolve kind=%s subject=%s", kind.kind, str(entity))
