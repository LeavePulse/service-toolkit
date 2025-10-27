"""Reusable health-check controller."""

from __future__ import annotations

from typing import ClassVar

from litestar import Controller, get


class HealthController(Controller):
    """Basic health-check endpoints."""

    path = "/health"
    tags: ClassVar[list[str]] = ["Health"]

    @get("/", include_in_schema=False, summary="Service health")
    async def health(self) -> dict[str, str]:
        return {"status": "healthy"}

    @get("/ready", include_in_schema=False, summary="Readiness probe")
    async def readiness(self) -> dict[str, str]:
        return {"status": "ready"}

    @get("/live", include_in_schema=False, summary="Liveness probe")
    async def liveness(self) -> dict[str, str]:
        return {"status": "alive"}


__all__ = ["HealthController"]
