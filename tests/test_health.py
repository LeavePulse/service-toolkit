"""What the probes must and must not say.

The property that matters: readiness has to be able to FAIL. It used to return
``{"status": "healthy"}` unconditionally, which reads as a harmless stub until
you notice the deploy agent gates a rollout on it — a probe that always passes
turns health-gated auto-rollback into a no-op, and a service with an unreachable
database deploys green.
"""

from __future__ import annotations

import asyncio

import pytest
from litestar.exceptions import ServiceUnavailableException

from service_toolkit.web.health import (
    HealthController,
    HealthState,
    sqlalchemy_check,
)


def _ok() -> object:
    async def check() -> str | None:
        return None

    return check


def _down(reason: str = "connection refused") -> object:
    async def check() -> str | None:
        return reason

    return check


def _raises() -> object:
    async def check() -> str | None:
        raise RuntimeError("boom")

    return check


def _hangs() -> object:
    async def check() -> str | None:
        await asyncio.sleep(60)
        return None

    return check


class _State:
    def __init__(self, health: HealthState | None) -> None:
        self.health = health


@pytest.mark.asyncio
async def test_ready_fails_when_a_dependency_is_down() -> None:
    state = _State(HealthState(checks={"postgres": _down()}))  # type: ignore[arg-type]

    with pytest.raises(ServiceUnavailableException) as exc:
        await HealthController.readiness.fn(HealthController, state=state)

    # The reason travels with the refusal, or whoever is paged reads the wrong
    # service's logs first.
    assert "postgres" in str(exc.value)
    assert "connection refused" in str(exc.value)


@pytest.mark.asyncio
async def test_ready_passes_when_everything_answers() -> None:
    state = _State(HealthState(checks={"postgres": _ok(), "nats": _ok()}))  # type: ignore[arg-type]

    result = await HealthController.readiness.fn(HealthController, state=state)

    assert result == {"status": "ready"}


@pytest.mark.asyncio
async def test_live_ignores_dependencies() -> None:
    """Liveness must not fail on a dependency: an orchestrator RESTARTS what
    fails liveness, and restarting a service because its database blinked turns
    a brief outage into a crash loop that outlives it."""
    result = await HealthController.liveness.fn(HealthController)

    assert result == {"status": "alive"}


@pytest.mark.asyncio
async def test_a_check_that_raises_is_down_not_a_500() -> None:
    state = _State(HealthState(checks={"nats": _raises()}))  # type: ignore[arg-type]

    with pytest.raises(ServiceUnavailableException) as exc:
        await HealthController.readiness.fn(HealthController, state=state)

    assert "nats" in str(exc.value)
    assert "boom" in str(exc.value)


@pytest.mark.asyncio
async def test_a_hanging_check_times_out() -> None:
    """A check that outlives the probe is a queue, not a check."""
    import service_toolkit.web.health as health_mod

    original = health_mod.CHECK_TIMEOUT_SECONDS
    health_mod.CHECK_TIMEOUT_SECONDS = 0.05
    try:
        state = _State(HealthState(checks={"slow": _hangs()}))  # type: ignore[arg-type]
        with pytest.raises(ServiceUnavailableException) as exc:
            await HealthController.readiness.fn(HealthController, state=state)
        assert "timed out" in str(exc.value)
    finally:
        health_mod.CHECK_TIMEOUT_SECONDS = original


@pytest.mark.asyncio
async def test_health_reports_identity_and_each_dependency() -> None:
    state = _State(
        HealthState(  # type: ignore[arg-type]
            service_name="dcim-service",
            version="sha-abc1234",
            checks={"postgres": _ok(), "nats": _down("no route")},
        )
    )

    body = await HealthController.health.fn(HealthController, state=state)

    assert body["service"] == "dcim-service"
    assert body["version"] == "sha-abc1234"
    # One dependency down means degraded, not healthy — the summary must not
    # round up.
    assert body["status"] == "degraded"
    by_name = {d["name"]: d for d in body["dependencies"]}
    assert by_name["postgres"]["status"] == "up"
    assert by_name["nats"]["status"] == "down"
    assert by_name["nats"]["detail"] == "no route"


@pytest.mark.asyncio
async def test_an_app_without_state_keeps_the_old_behaviour() -> None:
    """A service that has not opted in must not start failing readiness."""
    state = _State(None)

    assert await HealthController.readiness.fn(HealthController, state=state) == {
        "status": "ready"
    }
    assert await HealthController.health.fn(HealthController, state=state) == {
        "status": "healthy"
    }


@pytest.mark.asyncio
async def test_the_answer_is_cached_so_polling_does_not_hammer_the_database() -> None:
    calls = {"n": 0}

    async def counting() -> str | None:
        calls["n"] += 1
        return None

    health = HealthState(checks={"postgres": counting})
    for _ in range(5):
        await health.evaluate()

    assert calls["n"] == 1


def test_the_probes_work_on_a_real_app() -> None:
    """Exercised through an actual app, not by calling the handlers.

    Calling them directly passes whatever object the test hands over, so it
    cannot catch how Litestar itself treats the reserved ``state`` kwarg — it
    rejects any annotation that is not a State subclass, and the whole app
    fails to build. Every unit test above stayed green while every service
    refused to start.
    """
    from litestar.datastructures import State as LitestarState
    from litestar.testing import create_test_client

    app_state = LitestarState({"health": HealthState(checks={"postgres": _down()})})  # type: ignore[arg-type]
    with create_test_client(
        route_handlers=[HealthController], state=app_state
    ) as client:
        assert client.get("/health/live").status_code == 200
        # A down dependency has to surface as 503 over HTTP, since that is what
        # the deploy gate and a load balancer actually read.
        assert client.get("/health/ready").status_code == 503
        assert client.get("/health/").json()["status"] == "degraded"

    healthy = LitestarState({"health": HealthState(checks={"postgres": _ok()})})  # type: ignore[arg-type]
    with create_test_client(route_handlers=[HealthController], state=healthy) as client:
        assert client.get("/health/ready").status_code == 200

    # And an app that registered no state keeps the old unconditional answers.
    with create_test_client(route_handlers=[HealthController]) as client:
        assert client.get("/health/ready").status_code == 200
        assert client.get("/health/").json() == {"status": "healthy"}


def test_version_comes_from_the_environment_when_not_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The control plane sets SERVICE_VERSION to the deployed tag.

    A container knows its env but never the tag it was pulled by, so without
    this the field is empty and "which build is this" stays unanswerable from
    the service itself.
    """
    from service_toolkit.web.app_factory import create_service_app

    monkeypatch.setenv("SERVICE_VERSION", "sha-deadbee")
    app = create_service_app(
        service_name="probe-service",
        openapi_title="probe",
        route_handlers=[],
    )

    assert app.state.health.version == "sha-deadbee"
    assert app.state.health.service_name == "probe-service"

    # An explicit argument still wins: a service that pins its own version is
    # not overridden by the deploy tag. Built under a different service name
    # because Prometheus metrics register globally per name and a second app
    # sharing one would collide.
    pinned = create_service_app(
        service_name="pinned-service",
        openapi_title="probe",
        route_handlers=[],
        version="1.2.3",
    )
    assert pinned.state.health.version == "1.2.3"


@pytest.mark.asyncio
async def test_sqlalchemy_check_round_trips_a_statement() -> None:
    """The check has to ASK the server. A pooled connection can be checked out
    and dead, so reading pool metrics would report health that isn't there."""
    executed: list[str] = []

    class _Conn:
        async def execute(self, stmt: object) -> None:
            executed.append(str(stmt))

        async def __aenter__(self) -> _Conn:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

    class _Engine:
        def connect(self) -> _Conn:
            return _Conn()

    class _Config:
        def get_engine(self) -> _Engine:
            return _Engine()

    check = sqlalchemy_check(_Config())

    assert await check() is None
    assert executed == ["SELECT 1"]
