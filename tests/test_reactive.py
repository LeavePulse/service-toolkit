from __future__ import annotations

import asyncio
import re
from collections.abc import Iterable
from typing import Any

import msgspec
import pytest

from service_toolkit.reactive import (
    GONE,
    UNKNOWN,
    EntityRef,
    Facts,
    Fields,
    Gone,
    MemoryStore,
    MemoryTimers,
    PendingEffect,
    Reactor,
    Record,
    Transition,
    held_for,
    latch,
)

F = Fields()
M = "member"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Members:
    """A provider over a dict, with an optional join: rooms feed member facts."""

    def __init__(self) -> None:
        self.data: dict[str, dict[str, Any]] = {}
        self.is_ready = True
        self.joins: dict[str, list[str]] = {}
        self.removed: set[str] = set()

    def ref(self, ident: str) -> EntityRef:
        return EntityRef(M, ident)

    async def facts(self, entity: EntityRef) -> Facts | Gone | None:
        if entity.id in self.removed:
            return GONE
        row = self.data.get(entity.id)
        return None if row is None else Facts(entity, row)

    async def entities(self) -> list[EntityRef]:
        return [self.ref(i) for i in self.data]

    def ready(self) -> bool:
        return self.is_ready

    def dependents(self, entity: EntityRef, fields: frozenset[str]) -> Iterable[tuple[EntityRef, frozenset[str]]]:
        return [(self.ref(i), frozenset({"own_rooms"})) for i in self.joins.get(str(entity), [])]


def make(**kwargs: Any) -> tuple[Reactor, Members, Clock]:
    clock = Clock()
    reactor = Reactor(store=MemoryStore(), timers=MemoryTimers(), clock=clock, jitter=0, reconcile_rate=0, **kwargs)
    members = Members()
    reactor.provide(M, members)
    return reactor, members, clock


def facts(ident: str, version: int | None = None, **fields: Any) -> Facts:
    return Facts(EntityRef(M, ident), fields, version)


in_voice_staff = F.voice.present() & F.roles.contains("staff")


def test_three_valued_logic_and_explain() -> None:
    f = facts("1", voice="general")
    assert in_voice_staff.evaluate(f) is UNKNOWN
    assert (F.voice.present() | F.roles.contains("staff")).evaluate(f) is True
    assert (~F.roles.contains("staff")).evaluate(f) is UNKNOWN
    assert (F.voice.eq("other") & F.roles.contains("staff")).evaluate(f) is False
    text = in_voice_staff.explain(f).render()
    assert "roles contains 'staff' -> UNKNOWN" in text
    assert in_voice_staff.fields() == {"voice", "roles"}


def test_unknown_has_no_truth_value() -> None:
    with pytest.raises(TypeError):
        bool(UNKNOWN)


@pytest.mark.asyncio
async def test_first_evaluation_is_silent_then_transitions_fire() -> None:
    reactor, _, _ = make()
    seen: list[tuple[str, str]] = []

    @reactor.on_enter(in_voice_staff, kind=M)
    async def entered(entity: EntityRef, f: Facts, t: Transition) -> None:
        seen.append(("enter", t.id))

    @reactor.on_exit(in_voice_staff, kind=M)
    async def left(entity: EntityRef, f: Facts, t: Transition) -> None:
        seen.append(("exit", t.id))

    # Already matching when first seen: recorded, not fired.
    await reactor.changed(facts("1", voice="a", roles={"staff"}))
    assert seen == []
    # Joined voice without the role, got the role later: that is the enter.
    await reactor.changed(facts("2", voice="a", roles=set()))
    await reactor.changed(facts("2", voice="a", roles={"staff"}), fields={"roles"})
    await reactor.changed(facts("2", voice=None, roles={"staff"}), fields={"voice"})
    assert [k for k, _ in seen] == ["enter", "exit"]
    assert re.fullmatch(r"entered:member:2:[0-9a-f]{8}-1:enter", seen[0][1])


@pytest.mark.asyncio
async def test_unknown_changes_nothing() -> None:
    reactor, _, _ = make()
    exits: list[str] = []

    @reactor.on_exit(in_voice_staff, kind=M)
    async def left(entity: EntityRef, f: Facts, t: Transition) -> None:
        exits.append(t.id)

    await reactor.changed(facts("1", voice="a", roles={"staff"}))
    await reactor.changed(facts("1", voice="a"))  # roles missing: UNKNOWN
    assert exits == []


@pytest.mark.asyncio
async def test_unrelated_field_does_not_wake_the_rule() -> None:
    reactor, _, _ = make()
    calls = 0

    async def observe(entity: EntityRef) -> Any:
        nonlocal calls
        calls += 1
        return True

    @reactor.converge(F.voice.present(), kind=M, observe=observe)
    async def apply(entity: EntityRef, desired: Any) -> None:
        pass

    await reactor.changed(facts("1", voice="a", nick="x"), fields={"nick"})
    assert calls == 0
    await reactor.changed(facts("1", voice="a", nick="x"), fields={"voice"})
    assert calls == 1


@pytest.mark.asyncio
async def test_held_for_waits_and_pauses_while_unknown() -> None:
    reactor, members, clock = make()
    entered: list[str] = []

    @reactor.on_enter(held_for(F.voice.present(), minutes=5), kind=M)
    async def stayed(entity: EntityRef, f: Facts, t: Transition) -> None:
        entered.append(t.id)

    members.data["1"] = {"voice": None}
    await reactor.changed(facts("1", voice=None))  # silent baseline: not holding
    members.data["1"] = {"voice": "a"}
    await reactor.changed(facts("1", voice="a"))
    clock.now += 120
    members.data["1"] = {}  # data missing for two minutes: the clock pauses
    await reactor.changed(facts("1"))
    clock.now += 120
    members.data["1"] = {"voice": "a"}
    await reactor.changed(facts("1", voice="a"))
    clock.now += 170  # 290 s counted
    await reactor.run_timers()
    assert entered == []
    clock.now += 20  # 310 s counted
    await reactor.run_timers()
    assert len(entered) == 1


@pytest.mark.asyncio
async def test_held_for_reads_a_callable_duration_at_every_evaluation() -> None:
    reactor, members, clock = make()
    config = {"seconds": 600.0}
    entered: list[str] = []

    @reactor.on_enter(held_for(F.voice.present(), seconds=lambda: config["seconds"]), kind=M)
    async def stayed(entity: EntityRef, f: Facts, t: Transition) -> None:
        entered.append(t.id)

    members.data["1"] = {"voice": None}
    await reactor.changed(facts("1", voice=None))
    members.data["1"] = {"voice": "a"}
    await reactor.changed(facts("1", voice="a"))
    clock.now += 120
    config["seconds"] = 60.0  # shortened by a reload: the next look decides
    await reactor.changed(facts("1", voice="a"))
    assert len(entered) == 1
    with pytest.raises(ValueError):
        held_for(F.voice.present(), seconds=lambda: 1.0, minutes=1)


@pytest.mark.asyncio
async def test_held_for_grace_forgives_a_short_break() -> None:
    reactor, members, clock = make()
    seen: list[str] = []

    @reactor.on_enter(held_for(F.voice.present(), minutes=10, grace=300), kind=M)
    async def waited(entity: EntityRef, f: Facts, t: Transition) -> None:
        seen.append("enter")

    @reactor.on_exit(held_for(F.voice.present(), minutes=10, grace=300), kind=M)
    async def gave_up(entity: EntityRef, f: Facts, t: Transition) -> None:
        seen.append("exit")

    async def show(voice: str | None) -> None:
        members.data["1"] = {"voice": voice}
        await reactor.changed(facts("1", voice=voice))

    await show(None)
    await show("a")
    clock.now += 240
    await show(None)  # out for two minutes: forgiven, and the gap counts
    clock.now += 120
    await show("a")
    clock.now += 250  # 610 s since the start
    await reactor.run_timers()
    assert seen == ["enter"]
    await show(None)  # a short break after the enter changes nothing
    clock.now += 100
    await show("a")
    assert seen == ["enter"]
    await show(None)
    clock.now += 301  # the break outlived the grace: the run is over
    await reactor.run_timers()
    assert seen == ["enter", "exit"]
    await show("a")
    clock.now += 300
    await reactor.run_timers()
    assert seen == ["enter", "exit"]  # a new run counts from zero


@pytest.mark.asyncio
async def test_settle_absorbs_a_flap() -> None:
    reactor, members, clock = make()
    exits: list[str] = []

    @reactor.on_exit(F.voice.present(), kind=M, settle=3)
    async def left(entity: EntityRef, f: Facts, t: Transition) -> None:
        exits.append(t.id)

    members.data["1"] = {"voice": "a"}
    await reactor.changed(facts("1", voice="a"))
    # A move between channels: out, then in again a moment later.
    await reactor.changed(facts("1", voice=None))
    clock.now += 1
    await reactor.changed(facts("1", voice="b"))
    clock.now += 5
    members.data["1"] = {"voice": "b"}
    await reactor.run_timers()
    assert exits == []
    # A real leave commits once it has held for the settle time.
    members.data["1"] = {"voice": None}
    await reactor.changed(facts("1", voice=None))
    clock.now += 4
    await reactor.run_timers()
    assert len(exits) == 1


@pytest.mark.asyncio
async def test_latch_uses_different_conditions_in_and_out() -> None:
    reactor, _, _ = make()
    log: list[str] = []
    rule = latch(enter=F.dead_rounds.ge(2), exit=F.live_rounds.ge(8))

    @reactor.on_enter(rule, kind=M)
    async def failed_over(entity: EntityRef, f: Facts, t: Transition) -> None:
        log.append("over")

    @reactor.on_exit(rule, kind=M)
    async def restored(entity: EntityRef, f: Facts, t: Transition) -> None:
        log.append("back")

    await reactor.changed(facts("1", dead_rounds=0, live_rounds=10))
    await reactor.changed(facts("1", dead_rounds=2, live_rounds=0))
    await reactor.changed(facts("1", dead_rounds=0, live_rounds=3))  # healthy-looking, not proven
    assert log == ["over"]
    await reactor.changed(facts("1", dead_rounds=0, live_rounds=8))
    assert log == ["over", "back"]


@pytest.mark.asyncio
async def test_older_version_is_dropped() -> None:
    reactor, _, _ = make()
    entered: list[str] = []

    @reactor.on_enter(F.voice.present(), kind=M)
    async def joined(entity: EntityRef, f: Facts, t: Transition) -> None:
        entered.append(t.id)

    await reactor.changed(facts("1", version=5, voice=None))
    await reactor.changed(facts("1", version=4, voice="a"))  # late replay
    assert entered == []
    await reactor.changed(facts("1", version=6, voice="a"))
    assert len(entered) == 1


@pytest.mark.asyncio
async def test_converge_reads_back_and_retries_a_mismatch() -> None:
    reactor, members, clock = make()
    world: dict[str, bool] = {}
    broken = True

    async def observe(entity: EntityRef) -> Any:
        return world.get(entity.id, False)

    @reactor.converge(F.voice.present(), kind=M, observe=observe)
    async def apply(entity: EntityRef, desired: Any) -> None:
        if not broken:  # the call "succeeds" but changes nothing
            world[entity.id] = desired

    members.data["1"] = {"voice": "a"}
    await reactor.changed(facts("1", voice="a"))
    assert world == {}
    broken = False
    clock.now += 10
    await reactor.run_timers()
    assert world == {"1": True}


@pytest.mark.asyncio
async def test_converge_compares_with_the_target_not_with_what_was_sent() -> None:
    reactor, _, _ = make()
    world: dict[str, bool] = {}
    applied = 0

    async def observe(entity: EntityRef) -> Any:
        return world.get(entity.id, False)

    @reactor.converge(F.voice.present(), kind=M, observe=observe)
    async def apply(entity: EntityRef, desired: Any) -> None:
        nonlocal applied
        applied += 1
        world[entity.id] = desired

    await reactor.changed(facts("1", voice="a"))
    await reactor.changed(facts("1", voice="a"))
    assert applied == 1
    world.clear()  # the target restarted and lost it
    await reactor.changed(facts("1", voice="a"))
    assert applied == 2


@pytest.mark.asyncio
async def test_shadow_and_switch_act_on_nothing() -> None:
    off = {"switched"}
    reactor, _, _ = make(enabled=lambda name: name not in off)
    calls: list[str] = []

    async def observe(entity: EntityRef) -> Any:
        return False

    @reactor.converge(F.voice.present(), kind=M, observe=observe, mode="shadow")
    async def shadowed(entity: EntityRef, desired: Any) -> None:
        calls.append("shadow")

    @reactor.on_enter(F.voice.present(), kind=M, name="switched")
    async def switched(entity: EntityRef, f: Facts, t: Transition) -> None:
        calls.append("switched")

    await reactor.changed(facts("1", voice=None))
    await reactor.changed(facts("1", voice="a"))
    assert calls == []


@pytest.mark.asyncio
async def test_one_owner_per_resource_key() -> None:
    reactor, _, _ = make()
    applied: list[str] = []

    async def observe(entity: EntityRef) -> Any:
        return False

    for name in ("first", "second"):

        async def apply(entity: EntityRef, desired: Any, name: str = name) -> None:
            applied.append(name)

        reactor.converge(F.voice.present(), kind=M, observe=observe, name=name, owns=lambda e: [f"role:{e.id}"], verify=False)(apply)

    await reactor.changed(facts("1", voice="a"))
    assert applied == ["first"]


@pytest.mark.asyncio
async def test_failed_effect_is_retried_with_the_same_id() -> None:
    reactor, members, clock = make()
    attempts: list[str] = []

    @reactor.on_enter(F.voice.present(), kind=M)
    async def joined(entity: EntityRef, f: Facts, t: Transition) -> None:
        attempts.append(t.id)
        if len(attempts) == 1:
            raise RuntimeError("discord said no")

    members.data["1"] = {"voice": "a"}
    await reactor.changed(facts("1", voice=None))
    await reactor.changed(facts("1", voice="a"))
    clock.now += 10
    await reactor.run_timers()
    assert len(attempts) == 2 and attempts[0] == attempts[1]
    clock.now += 1000
    await reactor.run_timers()
    assert len(attempts) == 2


@pytest.mark.asyncio
async def test_converge_runs_before_enter_in_one_change() -> None:
    reactor, _, _ = make()
    order: list[str] = []

    async def observe(entity: EntityRef) -> Any:
        return False

    @reactor.on_enter(F.voice.present(), kind=M)
    async def announce(entity: EntityRef, f: Facts, t: Transition) -> None:
        order.append("enter")

    @reactor.converge(F.voice.present(), kind=M, observe=observe, verify=False)
    async def grant(entity: EntityRef, desired: Any) -> None:
        order.append("converge")

    await reactor.changed(facts("1", voice=None))
    order.clear()
    await reactor.changed(facts("1", voice="a"))
    assert order == ["converge", "enter"]


@pytest.mark.asyncio
async def test_follower_does_nothing() -> None:
    leader = False
    reactor, _, _ = make(is_leader=lambda: leader)
    seen: list[str] = []

    @reactor.on_enter(F.voice.present(), kind=M, initial="fire")
    async def joined(entity: EntityRef, f: Facts, t: Transition) -> None:
        seen.append(t.id)

    await reactor.changed(facts("1", voice="a"))
    assert seen == []
    assert await reactor.store.get("joined", "member:1") is None


@pytest.mark.asyncio
async def test_reconcile_catches_missed_changes_and_departures() -> None:
    reactor, members, _ = make()
    log: list[str] = []
    withdrawn: list[str] = []

    async def observe(entity: EntityRef) -> Any:
        return UNKNOWN

    async def withdraw(entity: EntityRef) -> None:
        withdrawn.append(entity.id)

    @reactor.converge(F.voice.present(), kind=M, observe=observe, withdraw=withdraw, verify=False)
    async def grant(entity: EntityRef, desired: Any) -> None:
        pass

    @reactor.on_enter(F.voice.present(), kind=M)
    async def joined(entity: EntityRef, f: Facts, t: Transition) -> None:
        log.append(f"enter {entity.id}")

    @reactor.on_exit(F.voice.present(), kind=M)
    async def left(entity: EntityRef, f: Facts, t: Transition) -> None:
        log.append(f"exit {entity.id}")

    members.data = {"1": {"voice": None}, "2": {"voice": "a"}}
    await reactor.reconcile()  # baseline, silent
    assert log == []
    # While nobody was listening: 1 joined, 2 left the server.
    members.data = {"1": {"voice": "a"}}
    members.removed = {"2"}
    await reactor.reconcile()
    assert sorted(log) == ["enter 1", "exit 2"]
    assert withdrawn == ["2"]
    # A restart with the store intact replays nothing.
    log.clear()
    await reactor.reconcile()
    assert log == []


@pytest.mark.asyncio
async def test_retiring_a_rule_withdraws_what_it_holds() -> None:
    reactor, members, _ = make()
    withdrawn: list[str] = []

    async def observe(entity: EntityRef) -> Any:
        return True

    async def withdraw(entity: EntityRef) -> None:
        withdrawn.append(entity.id)

    @reactor.converge(F.voice.present(), kind=M, observe=observe, withdraw=withdraw, name="access")
    async def grant(entity: EntityRef, desired: Any) -> None:
        pass

    await reactor.changed(facts("1", voice="a"))
    await reactor.retire("access")
    assert withdrawn == ["1"]


@pytest.mark.asyncio
async def test_a_joined_fact_wakes_its_dependents() -> None:
    reactor, members, _ = make()
    entered: list[str] = []
    in_own_room = F.voice.matches(lambda ch, f: ch in f["own_rooms"], reads=["own_rooms"])

    @reactor.on_enter(in_own_room, kind=M)
    async def at_home(entity: EntityRef, f: Facts, t: Transition) -> None:
        entered.append(entity.id)

    members.data["1"] = {"voice": "r1", "own_rooms": set()}
    await reactor.changed(facts("1", voice="r1", own_rooms=set()))
    # The room changes owner; the member's own facts did not change, the join did.
    members.joins["member:room-r1"] = ["1"]
    members.data["1"] = {"voice": "r1", "own_rooms": {"r1"}}
    await reactor.changed(Facts(EntityRef(M, "room-r1"), {"owner": "1"}), fields={"owner"})
    assert entered == ["1"]


@pytest.mark.asyncio
async def test_explain_names_what_was_read() -> None:
    reactor, members, _ = make()

    @reactor.on_enter(in_voice_staff, kind=M, name="staff_voice")
    async def joined(entity: EntityRef, f: Facts, t: Transition) -> None:
        pass

    members.data["1"] = {"voice": "a", "roles": set()}
    explained = await reactor.explain("staff_voice", EntityRef(M, "1"))
    assert explained is not None
    assert explained.value is False
    assert "roles contains 'staff' -> False" in explained.render()


@pytest.mark.asyncio
async def test_repeat_is_a_heartbeat_while_it_holds() -> None:
    reactor, members, clock = make()
    beats: list[str] = []

    @reactor.on_enter(held_for(F.occupied.truthy(), minutes=5), kind=M, repeat=3600)
    async def touch(entity: EntityRef, f: Facts, t: Transition) -> None:
        beats.append(t.id)

    members.data["1"] = {"occupied": False}
    await reactor.changed(facts("1", occupied=False))
    members.data["1"] = {"occupied": True}
    await reactor.changed(facts("1", occupied=True))
    clock.now += 301
    await reactor.run_timers()
    clock.now += 3600
    await reactor.run_timers()
    assert re.fullmatch(r"touch:member:1:[0-9a-f]{8}-1:enter", beats[0])
    assert beats[1] == f"{beats[0]}:r1"
    members.data["1"] = {"occupied": False}
    await reactor.changed(facts("1", occupied=False))
    clock.now += 7200
    await reactor.run_timers()
    assert len(beats) == 2


@pytest.mark.asyncio
async def test_remove_forgets_a_rule_and_keeps_its_state() -> None:
    reactor, _, _ = make()

    async def handler(entity: EntityRef, f: Facts, t: Transition) -> None:
        pass

    reactor.on_enter(F.voice.present(), kind=M, name="stay")(handler)
    await reactor.changed(facts("1", voice="a"))
    reactor.remove("stay")
    reactor.on_enter(F.voice.present(), kind=M, name="stay")(handler)
    record = await reactor.store.get("stay", "member:1")
    assert record is not None and record.holds is True


def edge_log(reactor: Reactor, requirement: Any, log: list[str], **kwargs: Any) -> None:
    async def entered(entity: EntityRef, f: Facts, t: Transition) -> None:
        log.append(f"enter {entity.id}")

    async def left(entity: EntityRef, f: Facts, t: Transition) -> None:
        log.append(f"exit {entity.id}")

    reactor.on_enter(requirement, kind=M, name="in", **kwargs)(entered)
    reactor.on_exit(requirement, kind=M, name="out")(left)


@pytest.mark.asyncio
async def test_a_provider_without_facts_is_unknown_and_only_gone_exits() -> None:
    reactor, members, _ = make()
    log: list[str] = []
    withdrawn: list[str] = []

    async def observe(entity: EntityRef) -> Any:
        return UNKNOWN

    async def withdraw(entity: EntityRef) -> None:
        withdrawn.append(entity.id)

    @reactor.converge(F.voice.present(), kind=M, observe=observe, withdraw=withdraw, verify=False)
    async def grant(entity: EntityRef, desired: Any) -> None:
        pass

    edge_log(reactor, F.voice.present(), log)
    members.data = {"1": {"voice": "a"}}
    await reactor.reconcile()
    # The source cannot say: not listed, no facts. Nothing exits, nothing is withdrawn.
    members.data = {}
    await reactor.refresh(members.ref("1"))
    await reactor.reconcile()
    await reactor.run_timers()
    assert (log, withdrawn) == ([], [])
    assert await reactor.store.get("out", "member:1") is not None
    # Said to be gone: now it leaves.
    members.removed = {"1"}
    await reactor.reconcile()
    assert (log, withdrawn) == (["exit 1"], ["1"])
    assert await reactor.store.get("out", "member:1") is None


@pytest.mark.asyncio
async def test_held_for_pauses_while_the_provider_has_no_facts() -> None:
    reactor, members, clock = make()
    log: list[str] = []
    edge_log(reactor, held_for(F.voice.present(), minutes=5), log)
    members.data["1"] = {"voice": None}
    await reactor.changed(facts("1", voice=None))
    members.data["1"] = {"voice": "a"}
    await reactor.changed(facts("1", voice="a"))
    clock.now += 60
    del members.data["1"]
    await reactor.refresh(members.ref("1"))
    clock.now += 600
    # The timer comes due while there are no facts: it pauses, it does not fire or withdraw.
    await reactor.run_timers()
    await reactor.reconcile()
    assert log == []
    members.data["1"] = {"voice": "a"}
    await reactor.refresh(members.ref("1"))
    clock.now += 230
    await reactor.run_timers()
    assert log == []
    clock.now += 20
    await reactor.run_timers()
    assert log == ["enter 1"]


@pytest.mark.asyncio
async def test_on_reconciled_runs_after_each_reconcile_and_survives_a_failure() -> None:
    reactor, members, _ = make(tick=0.01)
    calls: list[tuple[str, frozenset[EntityRef]]] = []

    async def broken(kind: str, entities: frozenset[EntityRef]) -> None:
        raise RuntimeError("consumer bug")

    async def hook(kind: str, entities: frozenset[EntityRef]) -> None:
        calls.append((kind, entities))

    reactor.on_reconciled(M, broken)
    cancel = reactor.on_reconciled(M, hook)
    reactor.on_reconciled("room", hook)
    members.data = {"1": {"voice": None}}
    # The reactor's own reconcile, on start.
    await reactor.start()
    for _ in range(100):
        if calls:
            break
        await asyncio.sleep(0.01)
    await reactor.stop()
    assert calls == [(M, frozenset({members.ref("1")}))]
    cancel()
    await reactor.reconcile()
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_transition_ids_do_not_repeat_after_the_store_is_lost() -> None:
    reactor, _, _ = make()
    ids: list[str] = []

    @reactor.on_enter(F.voice.present(), kind=M)
    async def joined(entity: EntityRef, f: Facts, t: Transition) -> None:
        ids.append(t.id)

    for _ in range(2):
        await reactor.changed(facts("1", voice=None))
        await reactor.changed(facts("1", voice="a"))
        reactor.store = MemoryStore()
    # Both were the first enter of a fresh record; the generation tells them apart.
    assert len(ids) == 2 and ids[0] != ids[1]
    assert ids[0].rsplit("-", 1)[1] == ids[1].rsplit("-", 1)[1] == "1:enter"


@pytest.mark.asyncio
async def test_a_record_from_before_generations_gets_one_and_keeps_it() -> None:
    reactor, _, _ = make()
    ids: list[str] = []

    @reactor.on_enter(F.voice.present(), kind=M)
    async def joined(entity: EntityRef, f: Facts, t: Transition) -> None:
        ids.append(t.id)

    await reactor.store.put("joined", "member:1", Record(holds=False, since=1.0, epoch=4))
    await reactor.changed(facts("1", voice="a"))
    record = await reactor.store.get("joined", "member:1")
    assert record is not None and record.generation is not None
    assert ids == [f"joined:member:1:{record.generation}-5:enter"]


@pytest.mark.asyncio
async def test_a_retried_effect_keeps_the_moment_and_epoch_of_its_crossing() -> None:
    reactor, members, clock = make()
    seen: list[Transition] = []

    @reactor.on_exit(F.voice.present(), kind=M)
    async def left(entity: EntityRef, f: Facts, t: Transition) -> None:
        seen.append(t)
        if len(seen) == 1:
            raise RuntimeError("discord said no")

    await reactor.changed(facts("1", voice="a"))
    await reactor.changed(facts("1", voice=None))
    crossed = clock.now
    clock.now += 10
    # Entered again before the retry: the epoch moved on, the exit's did not.
    await reactor.changed(facts("1", voice="a"))
    members.data["1"] = {"voice": "a"}
    await reactor.run_timers()
    assert len(seen) == 2 and seen[0].id == seen[1].id
    assert seen[1].at == crossed and seen[1].epoch == seen[0].epoch


@pytest.mark.asyncio
async def test_a_pending_effect_from_before_at_retries_at_the_time_of_retry() -> None:
    old = msgspec.json.decode(b'{"id": "joined:member:1:1:enter", "kind": "enter", "attempts": 1}', type=PendingEffect)
    assert (old.at, old.epoch) == (None, None)
    reactor, members, clock = make()
    seen: list[Transition] = []

    @reactor.on_enter(F.voice.present(), kind=M)
    async def joined(entity: EntityRef, f: Facts, t: Transition) -> None:
        seen.append(t)

    members.data["1"] = {"voice": "a"}
    await reactor.store.put("joined", "member:1", Record(holds=True, since=1.0, epoch=3, generation="ab", pending=(old,)))
    await reactor.timers.schedule("joined|member:1", clock.now)
    await reactor.run_timers()
    assert [(t.id, t.at, t.epoch) for t in seen] == [("joined:member:1:1:enter", clock.now, 3)]


@pytest.mark.asyncio
async def test_a_transition_carries_its_explanation() -> None:
    reactor, _, _ = make()
    seen: list[Transition] = []

    @reactor.on_enter(in_voice_staff, kind=M)
    async def joined(entity: EntityRef, f: Facts, t: Transition) -> None:
        seen.append(t)

    @reactor.on_exit(in_voice_staff, kind=M)
    async def left(entity: EntityRef, f: Facts, t: Transition) -> None:
        seen.append(t)

    await reactor.changed(facts("1", voice="a", roles=set()))
    await reactor.changed(facts("1", voice="a", roles={"staff"}))
    await reactor.changed(facts("1", voice=None, roles={"staff"}))
    entered, exited = (t.explanation for t in seen)
    assert entered is not None and entered.value is True
    assert "roles contains 'staff' -> True" in entered.render()
    assert exited is not None and exited.value is False
    assert "voice present -> False" in exited.render()


@pytest.mark.asyncio
async def test_outside_work_under_locked_runs_one_at_a_time_with_the_engine() -> None:
    reactor, members, _ = make()
    seen: list[str] = []

    @reactor.on_enter(F.voice.present(), kind=M, initial="fire")
    async def joined(entity: EntityRef, f: Facts, t: Transition) -> None:
        seen.append("enter")

    assert reactor.provider(M) is members
    assert reactor.provider("room") is None
    async with reactor.locked(members.ref("1")):
        task = asyncio.create_task(reactor.changed(facts("1", voice="a")))
        await asyncio.sleep(0.01)
        seen.append("outside")
    await task
    assert seen == ["outside", "enter"]


@pytest.mark.asyncio
async def test_a_served_hold_keeps_holding_while_its_facts_do() -> None:
    hold = {"minutes": 5.0}
    log: list[str] = []
    store, timers = MemoryStore(), MemoryTimers()
    clock = Clock()
    members = Members()

    def boot() -> Reactor:
        reactor = Reactor(store=store, timers=timers, clock=clock, jitter=0, reconcile_rate=0)
        reactor.provide(M, members)
        edge_log(reactor, held_for(F.voice.present(), seconds=lambda: hold["minutes"] * 60), log)
        return reactor

    reactor = boot()
    members.data["1"] = {"voice": None}
    await reactor.changed(facts("1", voice=None))
    members.data["1"] = {"voice": "a"}
    await reactor.changed(facts("1", voice="a"))
    clock.now += 301
    await reactor.run_timers()
    assert log == ["enter 1"]
    # A restart: a new reactor over the same store, a provider with no facts
    # yet, and then a longer configured hold. The crossing stands.
    reactor = boot()
    del members.data["1"]
    await reactor.reconcile()
    clock.now += 60
    hold["minutes"] = 60
    members.data["1"] = {"voice": "a"}
    await reactor.reconcile()
    await reactor.changed(facts("1", voice="a"))
    record = await store.get("out", "member:1")
    assert record is not None and record.holds is True
    assert log == ["enter 1"]
