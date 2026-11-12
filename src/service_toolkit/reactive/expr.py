"""Requirements as data the engine can read.

A requirement is built from fields and operators instead of written as a
function, for two reasons. The engine can see which fields it reads, which is
what replaces "subscribe to these events": a change to ``roles`` wakes only
the requirements that read ``roles``. And it can explain itself: every part of
the tree reports its own value and the facts it read, which is the answer to
"why did this member not get access".

Logic is three-valued. ``UNKNOWN`` passes through every operator (Kleene
logic: ``False & UNKNOWN`` is ``False``, ``True & UNKNOWN`` is ``UNKNOWN``), so
a requirement over data that has not arrived yet is UNKNOWN rather than false,
and the engine leaves the state alone instead of acting on a guess.

    F = Fields()
    in_voice = F.voice_channel.present()
    staff = F.roles.contains_any(lambda: config.staff_role_ids)
    rule = in_voice & staff & ~F.muted.eq(True)

Values may be callables: they are resolved at every evaluation, so a config
reload changes the requirement without registering it again.

Time lives at the top of a requirement only: ``held_for`` (true once the inner
requirement has held, unbroken, for a while) and ``latch`` (different
conditions to enter and to leave). They need memory of the past, which the
engine keeps per rule and entity, so they wrap a requirement rather than nest
inside one.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass, field
from typing import Any

from .facts import UNKNOWN, Facts, Unknown

__all__ = [
    "Explanation",
    "Expr",
    "Field",
    "Fields",
    "HeldFor",
    "Latch",
    "Requirement",
    "Truth",
    "held_for",
    "latch",
]

Truth = bool | Unknown


def _resolve(value: Any) -> Any:
    return value() if callable(value) else value


@dataclass(slots=True)
class Explanation:
    """One node of an evaluated requirement: what it is, its value, what it read."""

    label: str
    value: Truth
    reads: dict[str, Any] = field(default_factory=dict)
    children: list[Explanation] = field(default_factory=list)

    def render(self, indent: int = 0) -> str:
        """The tree as indented text, for a log line or an operator's screen."""
        reads = ", ".join(f"{k}={v!r}" for k, v in self.reads.items())
        line = f"{'  ' * indent}{self.label} -> {self.value!r}" + (f"  [{reads}]" if reads else "")
        return "\n".join([line, *(child.render(indent + 1) for child in self.children)])


class Expr(ABC):
    """A three-valued predicate over facts."""

    @abstractmethod
    def fields(self) -> frozenset[str]:
        """Every field this expression reads."""

    @abstractmethod
    def explain(self, facts: Facts) -> Explanation:
        """Evaluate, keeping every part's value and the facts it read."""

    def evaluate(self, facts: Facts) -> Truth:
        return self.explain(facts).value

    def __and__(self, other: Expr) -> Expr:
        return All(self, other)

    def __or__(self, other: Expr) -> Expr:
        return Any_(self, other)

    def __invert__(self) -> Expr:
        return Not(self)


class All(Expr):
    parts: tuple[Expr, ...]

    def __init__(self, *parts: Expr) -> None:
        flat: list[Expr] = []
        for part in parts:
            flat.extend(part.parts if isinstance(part, All) else [part])
        self.parts = tuple(flat)

    def fields(self) -> frozenset[str]:
        return frozenset().union(*(p.fields() for p in self.parts))

    def explain(self, facts: Facts) -> Explanation:
        children = [p.explain(facts) for p in self.parts]
        values = [c.value for c in children]
        value: Truth
        if any(v is False for v in values):
            value = False
        elif any(v is UNKNOWN for v in values):
            value = UNKNOWN
        else:
            value = True
        return Explanation("all of", value, children=children)


class Any_(Expr):
    parts: tuple[Expr, ...]

    def __init__(self, *parts: Expr) -> None:
        flat: list[Expr] = []
        for part in parts:
            flat.extend(part.parts if isinstance(part, Any_) else [part])
        self.parts = tuple(flat)

    def fields(self) -> frozenset[str]:
        return frozenset().union(*(p.fields() for p in self.parts))

    def explain(self, facts: Facts) -> Explanation:
        children = [p.explain(facts) for p in self.parts]
        values = [c.value for c in children]
        value: Truth
        if any(v is True for v in values):
            value = True
        elif any(v is UNKNOWN for v in values):
            value = UNKNOWN
        else:
            value = False
        return Explanation("any of", value, children=children)


class Not(Expr):
    def __init__(self, inner: Expr) -> None:
        self.inner = inner

    def fields(self) -> frozenset[str]:
        return self.inner.fields()

    def explain(self, facts: Facts) -> Explanation:
        child = self.inner.explain(facts)
        value: Truth = UNKNOWN if child.value is UNKNOWN else not child.value
        return Explanation("not", value, children=[child])


class Check(Expr):
    """A test on one field's value. UNKNOWN in, UNKNOWN out."""

    def __init__(self, name: str, label: str, test: Callable[[Any], bool], reads: Iterable[str] = ()) -> None:
        self.name = name
        self.label = label
        self.test = test
        self.extra = frozenset(reads)

    def fields(self) -> frozenset[str]:
        return frozenset({self.name}) | self.extra

    def explain(self, facts: Facts) -> Explanation:
        value = facts.get(self.name)
        reads = {self.name: value}
        if value is UNKNOWN:
            return Explanation(self.label, UNKNOWN, reads)
        return Explanation(self.label, bool(self.test(value)), reads)


class Match(Expr):
    """A test that may look at other fields too; they are named so they wake it."""

    def __init__(self, name: str, label: str, test: Callable[[Any, Facts], bool], reads: Iterable[str] = ()) -> None:
        self.name = name
        self.label = label
        self.test = test
        self.extra = tuple(reads)

    def fields(self) -> frozenset[str]:
        return frozenset({self.name, *self.extra})

    def explain(self, facts: Facts) -> Explanation:
        seen = {name: facts.get(name) for name in (self.name, *self.extra)}
        if any(value is UNKNOWN for value in seen.values()):
            return Explanation(self.label, UNKNOWN, seen)
        return Explanation(self.label, bool(self.test(seen[self.name], facts)), seen)


class Field:
    """A named fact. Its methods build the checks a requirement is made of."""

    def __init__(self, name: str) -> None:
        self.name = name

    def present(self) -> Expr:
        """Has a value: in a voice channel, has an address. ``None`` is absent."""
        return Check(self.name, f"{self.name} present", lambda v: v is not None)

    def truthy(self) -> Expr:
        return Check(self.name, f"{self.name}", bool)

    def eq(self, value: Any) -> Expr:
        return Check(self.name, f"{self.name} == {value!r}", lambda v: v == _resolve(value))

    def ne(self, value: Any) -> Expr:
        return Check(self.name, f"{self.name} != {value!r}", lambda v: v != _resolve(value))

    def gt(self, value: Any) -> Expr:
        return Check(self.name, f"{self.name} > {value!r}", lambda v: v > _resolve(value))

    def ge(self, value: Any) -> Expr:
        return Check(self.name, f"{self.name} >= {value!r}", lambda v: v >= _resolve(value))

    def lt(self, value: Any) -> Expr:
        return Check(self.name, f"{self.name} < {value!r}", lambda v: v < _resolve(value))

    def le(self, value: Any) -> Expr:
        return Check(self.name, f"{self.name} <= {value!r}", lambda v: v <= _resolve(value))

    def in_(self, values: Collection[Any] | Callable[[], Collection[Any]]) -> Expr:
        """The value is one of these."""
        return Check(self.name, f"{self.name} in …", lambda v: v in _resolve(values))

    def contains(self, value: Any) -> Expr:
        """A collection field holds this value."""
        return Check(self.name, f"{self.name} contains {value!r}", lambda v: _resolve(value) in v)

    def contains_any(self, values: Collection[Any] | Callable[[], Collection[Any]]) -> Expr:
        """A collection field holds at least one of these."""
        return Check(self.name, f"{self.name} contains any of …", lambda v: not set(v).isdisjoint(_resolve(values)))

    def matches(self, test: Callable[[Any, Facts], bool], *, label: str = "", reads: Iterable[str] = ()) -> Expr:
        """Anything else. ``reads`` names the other fields ``test`` looks at, so they wake it too."""
        return Match(self.name, label or f"{self.name} matches", test, reads)


class Fields:
    """``F.roles`` is ``Field("roles")``: a short way to name facts."""

    def __getattr__(self, name: str) -> Field:
        if name.startswith("__"):
            raise AttributeError(name)
        return Field(name)


@dataclass(frozen=True, slots=True)
class HeldFor:
    """True once ``inner`` has held, without a break, for ``seconds``.

    ``seconds`` may be a callable, resolved at every evaluation like the values
    of a check: a duration from config follows a reload. A run already timed
    is looked at again when its old due time comes, or sooner on any change.

    ``grace`` forgives a break: while ``inner`` has been false for less than
    that, the run goes on as if it had not been, the gap counts towards it and
    the outcome stays what it was. Waiting in a queue is like that: stepping
    out for a minute and back does not start the wait again.
    """

    inner: Expr
    seconds: float | Callable[[], float]
    grace: float = 0.0

    def fields(self) -> frozenset[str]:
        return self.inner.fields()

    def length(self) -> float:
        return max(0.0, float(_resolve(self.seconds)))


@dataclass(frozen=True, slots=True)
class Latch:
    """Becomes true when ``enter`` holds and stays true until ``exit`` holds."""

    enter: Expr
    exit: Expr

    def fields(self) -> frozenset[str]:
        return self.enter.fields() | self.exit.fields()


Requirement = Expr | HeldFor | Latch


def held_for(
    inner: Expr, *, seconds: float | Callable[[], float] = 0, minutes: float = 0, grace: float = 0.0,
) -> HeldFor:
    if grace < 0:
        raise ValueError("held_for grace cannot be negative")
    if callable(seconds):
        if minutes:
            raise ValueError("held_for takes either a callable or a fixed duration")
        return HeldFor(inner, seconds, grace)
    total = seconds + minutes * 60
    if total <= 0:
        raise ValueError("held_for needs a positive duration")
    return HeldFor(inner, total, grace)


def latch(*, enter: Expr, exit: Expr) -> Latch:
    return Latch(enter, exit)
