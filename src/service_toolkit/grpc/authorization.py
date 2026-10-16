"""Which caller may call which method: one decision per policy-listed RPC.

The platform token proves only that a call came from somewhere inside the
platform: every service holds the same one, so it cannot say which service is
calling, and a method guarded by it is open to all of them. A method listed in
a :class:`CallerPolicy` is decided here instead, and only here:

* the caller is named by its own secret, presented on ``x-internal-token``
  (the header clients already send), and compared in constant time against
  every configured secret;
* the method names, by its exact full path, the callers it admits; every other
  caller is refused, and the platform token is not a caller at all.

An absent or unknown credential is ``UNAUTHENTICATED``; a known caller the
method does not list is ``PERMISSION_DENIED``. A method the policy does not
list keeps the platform-token rule, as a migration rule only: a service moves
its methods into the policy, and a new method starts there.

The caller's secret is the seam where a certificate identity can later take
its place (the policy names callers, not secrets). The identity is resolved
from the credential alone: no metadata a caller sends about itself is read.

A *complete* policy (``complete=True``) has no migration rule: it decides
every method the server serves, startup refuses one that misses a served
method, and an unlisted method is denied. Only grpc health and reflection are
exempt, by exact service name (:data:`EXEMPT_SERVICES`). Each entry is in one
finite state:

* ``ENFORCE``: only the listed callers, by their own credentials;
* ``Observe(until)``: as ENFORCE for any caller credential, and in addition
  the shared platform token is admitted for this method alone, each admission
  logged at WARNING, until the end of ``until`` (UTC). After that the entry is
  ENFORCE with no restart. A critical entry cannot observe, and no ``until``
  lies more than :data:`OBSERVE_WINDOW` after the policy's ``declared_on``.
"""

from __future__ import annotations

import enum
import hmac
import logging
import re
from collections.abc import Callable, Iterable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final

import grpc
from google.protobuf import descriptor_pool

from .handlers import rebuild, wrap

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CallerIdentity:
    """A caller as its own credential names it, and nothing else.

    Built only by :meth:`CallerCredentials.resolve`; never from request
    metadata.
    """

    name: str

    def __str__(self) -> str:
        return self.name


#: The caller admitted for the RPC being served: set for the handler's
#: duration and reset after it. None for a call admitted on the shared platform
#: token (an observing entry, or a method outside any policy).
current_caller: ContextVar[CallerIdentity | None] = ContextVar(
    "current_caller", default=None
)

#: The services a complete policy leaves open, by exact name: probes and
#: schema discovery carry no caller credential and expose no data.
EXEMPT_SERVICES: Final = frozenset(
    {
        "grpc.health.v1.Health",
        "grpc.reflection.v1alpha.ServerReflection",
        "grpc.reflection.v1.ServerReflection",
    }
)

#: The longest an entry may observe after the policy's declared date.
OBSERVE_WINDOW: Final = timedelta(days=7)

_CALLER_NAME = re.compile(r"[a-z0-9][a-z0-9-]*")
_METHOD_PATH = re.compile(r"/([A-Za-z_][\w.]*\.[A-Za-z_]\w*)/([A-Za-z_]\w*)")


class CallerPolicyError(ValueError):
    """A policy or a set of caller credentials that cannot be enforced as written.

    Never carries a secret: messages name callers and methods only.
    """


class CallerCredentials:
    """The callers a service knows, each by its own secret.

    Names are lowercase service names. Every secret is non-empty and no two
    callers share one, or a presented secret would name two callers.
    """

    __slots__ = ("_entries",)

    def __init__(self, secrets: Mapping[str, str]) -> None:
        entries: list[tuple[str, bytes]] = []
        seen: dict[bytes, str] = {}
        for name, secret in secrets.items():
            if not _CALLER_NAME.fullmatch(name):
                msg = f"caller name {name!r} is not a lowercase service name"
                raise CallerPolicyError(msg)
            if not secret or secret != secret.strip():
                msg = f"caller {name!r} has an empty or blank-padded secret"
                raise CallerPolicyError(msg)
            encoded = secret.encode()
            if encoded in seen:
                msg = f"callers {seen[encoded]!r} and {name!r} share one secret"
                raise CallerPolicyError(msg)
            seen[encoded] = name
            entries.append((name, encoded))
        self._entries = tuple(entries)

    @property
    def names(self) -> frozenset[str]:
        return frozenset(name for name, _ in self._entries)

    def resolve(self, presented: str | None) -> CallerIdentity | None:
        """The caller *presented* names, or None.

        Every configured secret is compared, without stopping at a match, so
        the time taken does not say which entry matched or how far it got.
        """
        candidate = (presented or "").encode()
        found: str | None = None
        for name, secret in self._entries:
            if hmac.compare_digest(candidate, secret):
                found = name
        return CallerIdentity(found) if found is not None else None

    def __repr__(self) -> str:
        return f"CallerCredentials(callers={sorted(self.names)!r})"


class Verdict(enum.Enum):
    ADMIT = "admit"
    #: Admitted on the shared platform token by an observing entry.
    ADMIT_SHARED = "admit_shared"
    UNAUTHENTICATED = "unauthenticated"
    PERMISSION_DENIED = "permission_denied"


@dataclass(frozen=True, slots=True)
class Decision:
    verdict: Verdict
    caller: CallerIdentity | None = None


@dataclass(frozen=True, slots=True)
class Enforce:
    """Only the entry's callers, by their own credentials."""


#: The one ENFORCE state.
ENFORCE: Final = Enforce()


@dataclass(frozen=True, slots=True)
class Observe:
    """As ENFORCE, and the shared platform token is admitted (and logged)
    through the end of *until*, UTC."""

    until: date


type RuleState = Enforce | Observe


@dataclass(frozen=True, slots=True)
class Rule:
    """One method's entry: who may call it, and in which state.

    *critical* marks a method whose misuse needs no second step (minting a
    token, approving a login); such an entry is always ENFORCE.
    """

    callers: frozenset[str]
    state: RuleState = ENFORCE
    critical: bool = False


def _utc_today() -> date:
    return datetime.now(UTC).date()


def _service_of(method: str) -> str | None:
    match = _METHOD_PATH.fullmatch(method)
    return match.group(1) if match else None


class CallerPolicy:
    """Exact method paths, each with the callers it admits.

    Construction refuses what could never be enforced: a malformed path, a
    method with no caller (outside a complete policy), a caller with no
    configured secret, a critical entry that observes, or an observing entry
    whose grace is undated or longer than :data:`OBSERVE_WINDOW`. Whether each
    method exists on the server is checked by :meth:`check_served` once the
    servicers are registered.

    A plain ``Iterable[str]`` value is an ENFORCE entry, as it always was.
    """

    __slots__ = ("_callers", "_complete", "_rules", "_today")

    def __init__(
        self,
        rules: Mapping[str, Iterable[str] | Rule],
        callers: CallerCredentials,
        *,
        complete: bool = False,
        declared_on: date | None = None,
        today: Callable[[], date] = _utc_today,
    ) -> None:
        parsed: dict[str, Rule] = {}
        for method, entry in rules.items():
            if not _METHOD_PATH.fullmatch(method):
                msg = f"{method!r} is not an exact '/package.Service/Method' path"
                raise CallerPolicyError(msg)
            rule = entry if isinstance(entry, Rule) else Rule(frozenset(entry))
            if not rule.callers and not complete:
                msg = f"{method} admits no caller"
                raise CallerPolicyError(msg)
            unknown = rule.callers - callers.names
            if unknown:
                msg = f"{method} admits {sorted(unknown)!r}, which have no secret"
                raise CallerPolicyError(msg)
            if isinstance(rule.state, Observe):
                _check_grace(method, rule.state, rule.critical, declared_on)
            parsed[method] = rule
        self._rules = parsed
        self._callers = callers
        self._complete = complete
        self._today = today

    @property
    def complete(self) -> bool:
        """Whether this policy decides every served method."""
        return self._complete

    @property
    def has_credentials(self) -> bool:
        return bool(self._callers.names)

    def covers(self, method: str) -> bool:
        return method in self._rules

    def exempt(self, method: str) -> bool:
        """Whether *method* belongs to a service a complete policy leaves open."""
        return _service_of(method) in EXEMPT_SERVICES

    def names_a_caller(self, secret: str) -> bool:
        """Whether *secret* is some caller's credential."""
        return self._callers.resolve(secret) is not None

    def observing(self, method: str) -> Observe | None:
        """*method*'s observing state while it is still in force, else None."""
        state = self._rules[method].state
        if isinstance(state, Observe) and self._today() <= state.until:
            return state
        return None

    def required_callers(self, method: str) -> frozenset[str]:
        return self._rules[method].callers

    def decide(
        self,
        method: str,
        presented: str | None,
        *,
        shared_token: str | None = None,
    ) -> Decision:
        """The one decision for a policy-listed *method*.

        A caller credential is decided by the entry's callers in every state.
        Only an observing entry, while in force, also admits *shared_token*.
        """
        caller = self._callers.resolve(presented)
        if caller is not None:
            if caller.name not in self._rules[method].callers:
                return Decision(Verdict.PERMISSION_DENIED, caller)
            return Decision(Verdict.ADMIT, caller)
        if (
            shared_token
            and self.observing(method) is not None
            and hmac.compare_digest((presented or "").encode(), shared_token.encode())
        ):
            return Decision(Verdict.ADMIT_SHARED)
        return Decision(Verdict.UNAUTHENTICATED)

    def check_served(self, service_names: Iterable[str]) -> None:
        """Refuse to serve when a listed method is not one this server serves,
        and, for a complete policy, when a served method is not listed.

        *service_names* are the fully qualified services registered on the
        server; each method must exist in that service's descriptor, so a typo
        or a renamed RPC fails at startup instead of leaving the real method
        under the platform token (or, in a complete policy, denied).
        """
        served = set(service_names)
        pool = descriptor_pool.Default()
        for method in self._rules:
            match = _METHOD_PATH.fullmatch(method)
            if match is None:  # pragma: no cover - refused in __init__
                msg = f"{method!r} is not an exact '/package.Service/Method' path"
                raise CallerPolicyError(msg)
            service, name = match.groups()
            if service not in served:
                msg = f"{method}: {service} is not served by this server"
                raise CallerPolicyError(msg)
            if name not in _descriptor(pool, service, method).methods_by_name:
                msg = f"{method}: {service} has no method {name}"
                raise CallerPolicyError(msg)
        if not self._complete:
            return
        unlisted = sorted(
            f"/{service}/{name}"
            for service in served - EXEMPT_SERVICES
            for name in _descriptor(pool, service, service).methods_by_name
            if f"/{service}/{name}" not in self._rules
        )
        if unlisted:
            msg = f"served but not listed in the complete policy: {', '.join(unlisted)}"
            raise CallerPolicyError(msg)

    def __repr__(self) -> str:
        return (
            f"CallerPolicy(methods={sorted(self._rules)!r}, "
            f"complete={self._complete!r})"
        )


def _check_grace(
    method: str, state: Observe, critical: bool, declared_on: date | None
) -> None:
    until = state.until
    if critical:
        msg = f"{method} is critical and cannot observe"
        raise CallerPolicyError(msg)
    if declared_on is None:
        msg = f"{method} observes, but the policy has no declared_on date"
        raise CallerPolicyError(msg)
    if until < declared_on:
        msg = f"{method} observes until {until}, before declared_on {declared_on}"
        raise CallerPolicyError(msg)
    if until > declared_on + OBSERVE_WINDOW:
        msg = (
            f"{method} observes until {until}, more than "
            f"{OBSERVE_WINDOW.days} days after declared_on {declared_on}"
        )
        raise CallerPolicyError(msg)


def _descriptor(pool: Any, service: str, subject: str) -> Any:
    try:
        return pool.FindServiceByName(service)
    except KeyError:
        msg = f"{subject}: {service} has no descriptor"
        raise CallerPolicyError(msg) from None


_REFUSALS = {
    Verdict.UNAUTHENTICATED: (grpc.StatusCode.UNAUTHENTICATED, "unknown caller"),
    Verdict.PERMISSION_DENIED: (
        grpc.StatusCode.PERMISSION_DENIED,
        "this caller may not call this method",
    ),
}


def refusing(
    handler: grpc.RpcMethodHandler, code: grpc.StatusCode, details: str
) -> grpc.RpcMethodHandler:
    """A handler of *handler*'s own kind that aborts with *code*.

    The kind has to match: a unary handler returned for a streaming method is
    not a refusal the client can read, it is a broken call.
    """

    # A coroutine for every kind: grpc.aio accepts one for a streaming
    # response too (it would write with context.write), and an abort raised
    # from an async generator on a bidirectional call can surface to the
    # client as "Internal error from Core" instead of the status set here.
    async def _abort(request: Any, context: grpc.aio.ServicerContext) -> None:
        await context.abort(code, details)

    return rebuild(handler, _abort)


def as_caller(
    handler: grpc.RpcMethodHandler, caller: CallerIdentity
) -> grpc.RpcMethodHandler:
    """*handler* with :data:`current_caller` set to *caller* while it runs,
    in the handler's own kind and execution model (:func:`.handlers.wrap`)."""

    def enter(_context: Any) -> Callable[[BaseException | None], None]:
        token = current_caller.set(caller)
        return lambda _failure: current_caller.reset(token)

    return wrap(handler, enter=enter)


async def authorize_listed(
    policy: CallerPolicy,
    continuation: Any,
    details: grpc.HandlerCallDetails,
    *,
    shared_token: str | None = None,
) -> Any:
    """Serve or refuse a policy-listed call: the whole decision, no fallback.

    *shared_token* is the platform token, consulted only for an entry that is
    observing (see :class:`Observe`).
    """
    method = details.method or ""
    metadata = dict(details.invocation_metadata or [])
    decision = policy.decide(
        method, metadata.get("x-internal-token"), shared_token=shared_token
    )
    handler = await continuation(details)
    if handler is None:
        return None
    if decision.verdict is Verdict.ADMIT and decision.caller is not None:
        return as_caller(handler, decision.caller)
    if decision.verdict is Verdict.ADMIT_SHARED:
        observing = policy.observing(method)
        logger.warning(
            "gRPC %s admitted on the shared platform token (observing until %s); "
            "required callers: %s",
            method,
            observing.until if observing else "?",
            ", ".join(sorted(policy.required_callers(method))) or "none",
        )
        return handler
    # Anything but an admission is a refusal: fail closed.
    code, text = _REFUSALS.get(decision.verdict, _REFUSALS[Verdict.UNAUTHENTICATED])
    logger.warning(
        "gRPC %s refused for %s (%s)",
        method,
        decision.caller or "an unknown caller",
        decision.verdict.value,
    )
    return refusing(handler, code, text)


async def deny_unlisted(continuation: Any, details: grpc.HandlerCallDetails) -> Any:
    """Refuse a method a complete policy does not list, whoever calls it."""
    handler = await continuation(details)
    if handler is None:
        return None
    logger.warning(
        "gRPC %s refused: not listed in the complete caller policy", details.method
    )
    return refusing(
        handler,
        grpc.StatusCode.PERMISSION_DENIED,
        "this method is not open to any caller",
    )


__all__ = [
    "ENFORCE",
    "EXEMPT_SERVICES",
    "OBSERVE_WINDOW",
    "CallerCredentials",
    "CallerIdentity",
    "CallerPolicy",
    "CallerPolicyError",
    "Decision",
    "Enforce",
    "Observe",
    "Rule",
    "RuleState",
    "Verdict",
    "as_caller",
    "authorize_listed",
    "current_caller",
    "deny_unlisted",
    "refusing",
]
