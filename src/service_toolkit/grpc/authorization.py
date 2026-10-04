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
its place (the policy names callers, not secrets).
"""

from __future__ import annotations

import enum
import hmac
import logging
import re
from collections.abc import Callable, Iterable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

import grpc
from google.protobuf import descriptor_pool

from .handlers import rebuild, wrap

logger = logging.getLogger(__name__)

#: The caller admitted for the RPC being served, for audit logging only: set
#: for the handler's duration and reset after it. Never an authorization input.
current_caller: ContextVar[str | None] = ContextVar("current_caller", default=None)

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

    def resolve(self, presented: str | None) -> str | None:
        """The caller *presented* names, or None.

        Every configured secret is compared, without stopping at a match, so
        the time taken does not say which entry matched or how far it got.
        """
        candidate = (presented or "").encode()
        found: str | None = None
        for name, secret in self._entries:
            if hmac.compare_digest(candidate, secret):
                found = name
        return found

    def __repr__(self) -> str:
        return f"CallerCredentials(callers={sorted(self.names)!r})"


class Verdict(enum.Enum):
    ADMIT = "admit"
    UNAUTHENTICATED = "unauthenticated"
    PERMISSION_DENIED = "permission_denied"


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    caller: str | None = None


class CallerPolicy:
    """Exact method paths, each with the callers it admits.

    Construction refuses what could never be enforced: a malformed path, a
    method with no caller, or a caller with no configured secret. Whether each
    method exists on the server is checked by :meth:`check_served` once the
    servicers are registered.
    """

    __slots__ = ("_callers", "_rules")

    def __init__(
        self,
        rules: Mapping[str, Iterable[str]],
        callers: CallerCredentials,
    ) -> None:
        parsed: dict[str, frozenset[str]] = {}
        for method, admitted in rules.items():
            if not _METHOD_PATH.fullmatch(method):
                msg = f"{method!r} is not an exact '/package.Service/Method' path"
                raise CallerPolicyError(msg)
            names = frozenset(admitted)
            if not names:
                msg = f"{method} admits no caller"
                raise CallerPolicyError(msg)
            unknown = names - callers.names
            if unknown:
                msg = f"{method} admits {sorted(unknown)!r}, which have no secret"
                raise CallerPolicyError(msg)
            parsed[method] = names
        self._rules = parsed
        self._callers = callers

    def covers(self, method: str) -> bool:
        return method in self._rules

    def decide(self, method: str, presented: str | None) -> Decision:
        """The one decision for a policy-listed *method*."""
        caller = self._callers.resolve(presented)
        if caller is None:
            return Decision(Verdict.UNAUTHENTICATED)
        if caller not in self._rules[method]:
            return Decision(Verdict.PERMISSION_DENIED, caller)
        return Decision(Verdict.ADMIT, caller)

    def check_served(self, service_names: Iterable[str]) -> None:
        """Refuse to serve when a listed method is not one this server serves.

        *service_names* are the fully qualified services registered on the
        server; each method must exist in that service's descriptor, so a typo
        or a renamed RPC fails at startup instead of leaving the real method
        under the platform token.
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
            try:
                descriptor = pool.FindServiceByName(service)
            except KeyError:
                msg = f"{method}: {service} has no descriptor"
                raise CallerPolicyError(msg) from None
            if name not in descriptor.methods_by_name:
                msg = f"{method}: {service} has no method {name}"
                raise CallerPolicyError(msg)

    def __repr__(self) -> str:
        return f"CallerPolicy(methods={sorted(self._rules)!r})"


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


def as_caller(handler: grpc.RpcMethodHandler, caller: str) -> grpc.RpcMethodHandler:
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
) -> Any:
    """Serve or refuse a policy-listed call: the whole decision, no fallback."""
    method = details.method or ""
    metadata = dict(details.invocation_metadata or [])
    decision = policy.decide(method, metadata.get("x-internal-token"))
    handler = await continuation(details)
    if handler is None:
        return None
    if decision.verdict is Verdict.ADMIT and decision.caller is not None:
        return as_caller(handler, decision.caller)
    # Anything but a named admission is a refusal: fail closed.
    code, text = _REFUSALS.get(decision.verdict, _REFUSALS[Verdict.UNAUTHENTICATED])
    logger.warning(
        "gRPC %s refused for %s (%s)",
        method,
        decision.caller or "an unknown caller",
        decision.verdict.value,
    )
    return refusing(handler, code, text)


__all__ = [
    "CallerCredentials",
    "CallerPolicy",
    "CallerPolicyError",
    "Decision",
    "Verdict",
    "as_caller",
    "authorize_listed",
    "current_caller",
    "refusing",
]
