"""Requirements instead of event handlers.

A function declares when it applies, over the facts of an entity; the engine
works out which changes can affect that, watches them, and checks the whole
state again whenever it cannot trust that it saw every change (start,
reconnect, gaining leadership). Two kinds of effect: ``converge`` keeps a
desired state, idempotently; ``on_enter`` / ``on_exit`` act once per
transition, at least once, with a stable id for idempotency downstream.

The core knows no platform: an adapter turns a platform's events into
``Facts`` and hands them to ``Reactor.changed``.
"""

from __future__ import annotations

from .engine import Provider, Reactor
from .expr import Explanation, Expr, Field, Fields, HeldFor, Latch, Requirement, Truth, held_for, latch
from .facts import GONE, UNKNOWN, EntityRef, Facts, Gone, Unknown
from .rules import ConvergeRule, EdgeRule, Mode, Transition
from .store import MemoryStore, MemoryTimers, PendingEffect, Record, RedisStore, RedisTimers, Timers, TransitionStore

__all__ = [
    "GONE",
    "UNKNOWN",
    "ConvergeRule",
    "EdgeRule",
    "EntityRef",
    "Explanation",
    "Expr",
    "Facts",
    "Field",
    "Fields",
    "Gone",
    "HeldFor",
    "Latch",
    "MemoryStore",
    "MemoryTimers",
    "Mode",
    "PendingEffect",
    "Provider",
    "Reactor",
    "Record",
    "RedisStore",
    "RedisTimers",
    "Requirement",
    "Timers",
    "Transition",
    "TransitionStore",
    "Truth",
    "Unknown",
    "held_for",
    "latch",
]
