"""Event envelope helpers.

North Star: services publish facts (events) over NATS JetStream using a shared
envelope to support schema evolution and idempotent consumption.

This module intentionally avoids any NATS dependency so it can be reused by
HTTP-only services as well.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..ids.snowflake import generate_id


def utc_now_iso() -> str:
    """Return an ISO-8601 timestamp in UTC."""

    return datetime.now(UTC).isoformat()


def build_event(
    *,
    event_type: str,
    data: Any,
    producer: str | None = None,
    schema_version: int = 1,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    event_id: str | None = None,
    occurred_at: str | None = None,
) -> dict[str, Any]:
    """Build a standard event envelope.

    Args:
        event_type: Logical type of the event (usually matches the NATS subject).
        data: Event payload.
        producer: Service name that produced the event.
        schema_version: Payload schema version.
        correlation_id: Optional trace/correlation identifier.
        causation_id: Optional identifier of the triggering event/command.
        event_id: Optional explicit event identifier (defaults to Snowflake id).
        occurred_at: Optional explicit ISO timestamp (defaults to now in UTC).
    """

    resolved_event_id = str(event_id or generate_id())
    resolved_occurred_at = str(occurred_at or utc_now_iso())

    return {
        "event_id": resolved_event_id,
        "event_type": str(event_type),
        "occurred_at": resolved_occurred_at,
        "producer": producer,
        "schema_version": int(schema_version),
        "correlation_id": correlation_id,
        "causation_id": causation_id,
        "data": data,
    }


__all__ = ["build_event", "utc_now_iso"]
