"""Health endpoints that answer honestly.

The three probes answer three different questions, and conflating them is the
mistake this module exists to prevent:

* **live** — is the process running? Nothing else. A dependency being down must
  NOT fail this: an orchestrator restarts a container that fails liveness, and
  restarting a service because its database blinked turns a brief outage into a
  crash loop that outlives it.
* **ready** — can this instance serve a request right now? That means its
  dependencies answer. A service whose database is unreachable is running fine
  and cannot do its job; saying "healthy" there is a lie with consequences —
  the deploy agent health-gates a rollout on this probe and rolls back when it
  fails, so a probe that always passes silently disables the rollback.
* **health** — the human-facing summary: what I am, what version, how long I
  have been up, and the state of each dependency by name.

Checks are cheap and bounded. Each runs under a timeout, and the result is
cached for a couple of seconds so a polling orchestrator cannot turn readiness
into load on the database it is asking about.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from litestar import Controller, get
from litestar.exceptions import ServiceUnavailableException

logger = logging.getLogger(__name__)

#: How long a single dependency check may take before it counts as down. Well
#: under any sensible probe interval — a check that outlives the probe that
#: started it is a queue, not a check.
CHECK_TIMEOUT_SECONDS = 2.0

#: How long a computed answer is reused. Readiness is polled continuously by
#: agents and load balancers; without this every poll would reach the database.
CACHE_TTL_SECONDS = 2.0


@dataclass(frozen=True, slots=True)
class DependencyStatus:
    """One dependency's verdict."""

    name: str
    ok: bool
    detail: str = ""


@dataclass
class _Cached:
    at: float = 0.0
    statuses: tuple[DependencyStatus, ...] = ()


@dataclass
class HealthState:
    """What the probes report on. Built once at app startup.

    ``checks`` maps a dependency name to a coroutine returning None on success
    or a short reason on failure. Services add their own (NATS, an upstream
    gRPC service) without this module knowing what they are.
    """

    service_name: str = "service"
    version: str = ""
    started_at: float = field(default_factory=time.time)
    checks: dict[str, Callable[[], Awaitable[str | None]]] = field(
        default_factory=dict
    )
    _cache: _Cached = field(default_factory=_Cached)

    async def evaluate(self) -> tuple[DependencyStatus, ...]:
        """Run every check, with the cache in front."""
        now = time.monotonic()
        if self._cache.statuses and (now - self._cache.at) < CACHE_TTL_SECONDS:
            return self._cache.statuses
        statuses = await asyncio.gather(
            *(self._run(name, fn) for name, fn in self.checks.items())
        )
        result = tuple(statuses)
        self._cache = _Cached(at=now, statuses=result)
        return result

    @staticmethod
    async def _run(
        name: str, fn: Callable[[], Awaitable[str | None]]
    ) -> DependencyStatus:
        """A check that raises or hangs is a failed check, never an exception
        escaping the probe — a 500 from the health endpoint tells a caller far
        less than "postgres: timed out"."""
        try:
            reason = await asyncio.wait_for(fn(), timeout=CHECK_TIMEOUT_SECONDS)
        except TimeoutError:
            logger.warning("health check %s timed out", name)
            return DependencyStatus(name=name, ok=False, detail="timed out")
        except Exception as exc:  # noqa: BLE001 - any failure means "down"
            # Logged with the traceback, not just summarised into the response:
            # the probe's caller sees "postgres: down", and whoever debugs it
            # needs to know which call failed and why.
            logger.warning("health check %s failed", name, exc_info=True)
            return DependencyStatus(name=name, ok=False, detail=str(exc)[:200])
        if reason:
            return DependencyStatus(name=name, ok=False, detail=reason)
        return DependencyStatus(name=name, ok=True)


def sqlalchemy_check(
    sqlalchemy_config: Any,
) -> Callable[[], Awaitable[str | None]]:
    """A database check: can we get a connection and round-trip a statement?

    ``SELECT 1`` rather than a pool-metrics read, because a pooled connection
    can be checked out and dead — the question is whether the server answers,
    and only a query asks it.
    """

    async def check() -> str | None:
        from sqlalchemy import text

        engine = sqlalchemy_config.get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return None

    return check


class HealthController(Controller):
    """Health, readiness and liveness endpoints."""

    path = "/health"
    tags = ["Health"]

    @get("/", include_in_schema=False, summary="Service health")
    async def health(self, state: Any) -> dict[str, Any]:
        """Everything known about this instance, for a human or a panel."""
        health_state = _state_of(state)
        if health_state is None:
            return {"status": "healthy"}
        statuses = await health_state.evaluate()
        return {
            "status": "healthy" if all(s.ok for s in statuses) else "degraded",
            "service": health_state.service_name,
            "version": health_state.version,
            "uptime_seconds": int(time.time() - health_state.started_at),
            "dependencies": [
                {"name": s.name, "status": "up" if s.ok else "down", "detail": s.detail}
                for s in statuses
            ],
        }

    @get("/ready", include_in_schema=False, summary="Readiness probe")
    async def readiness(self, state: Any) -> dict[str, Any]:
        """503 when a dependency is down, so a rollout gate can act on it."""
        health_state = _state_of(state)
        if health_state is None:
            return {"status": "ready"}
        statuses = await health_state.evaluate()
        down = [s for s in statuses if not s.ok]
        if down:
            # The reason travels with the refusal: "not ready" alone sends
            # whoever is debugging to the logs of the wrong service.
            detail = ", ".join(f"{s.name}: {s.detail or 'down'}" for s in down)
            raise ServiceUnavailableException(f"dependencies unavailable — {detail}")
        return {"status": "ready"}

    @get("/live", include_in_schema=False, summary="Liveness probe")
    async def liveness(self) -> dict[str, str]:
        """Deliberately unconditional — see the module docstring."""
        return {"status": "alive"}


def _state_of(state: Any) -> HealthState | None:
    """The app's HealthState, or None for an app that never registered one.

    Absent state means the old unconditional behaviour, so a service that has
    not opted in keeps working exactly as before.
    """
    return getattr(state, "health", None)


__all__ = [
    "CACHE_TTL_SECONDS",
    "CHECK_TIMEOUT_SECONDS",
    "DependencyStatus",
    "HealthController",
    "HealthState",
    "sqlalchemy_check",
]
