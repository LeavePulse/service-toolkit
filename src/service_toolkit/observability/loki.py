"""Small typed client for Loki's bounded range-query API.

This module deliberately does not accept a user-supplied LogQL expression from
an HTTP request.  The owning domain builds an allowlisted selector and passes
it here; the toolkit owns only transport, decoding and a stable result shape.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

import httpx
import msgspec

from ..web.http import build_shared_async_client

QueryDirection = Literal["forward", "backward"]


class LokiQueryError(RuntimeError):
    """Loki rejected a query or returned a response outside its public shape."""


class LokiLogLine(msgspec.Struct, frozen=True):
    """One log record, flattened from Loki's stream-oriented response."""

    timestamp_ns: int
    line: str
    labels: dict[str, str]


class LokiQueryResult(msgspec.Struct, frozen=True):
    """A bounded set of log records returned by a Loki range query."""

    lines: list[LokiLogLine]


def _positive(name: str, value: int) -> int:
    if value <= 0:
        msg = f"{name} must be positive"
        raise ValueError(msg)
    return value


def _decode_lines(payload: Mapping[str, object], *, direction: QueryDirection) -> list[LokiLogLine]:
    if payload.get("status") != "success":
        msg = "Loki returned an unsuccessful query response"
        raise LokiQueryError(msg)
    data = payload.get("data")
    if not isinstance(data, Mapping) or data.get("resultType") != "streams":
        msg = "Loki returned a non-stream query response"
        raise LokiQueryError(msg)
    streams = data.get("result")
    if not isinstance(streams, list):
        msg = "Loki returned malformed stream results"
        raise LokiQueryError(msg)

    lines: list[LokiLogLine] = []
    for stream in streams:
        if not isinstance(stream, Mapping):
            raise LokiQueryError("Loki returned a malformed stream")
        raw_labels = stream.get("stream")
        raw_values = stream.get("values")
        if not isinstance(raw_labels, Mapping) or not isinstance(raw_values, list):
            raise LokiQueryError("Loki returned malformed stream data")
        labels = {str(key): str(value) for key, value in raw_labels.items()}
        for raw_value in raw_values:
            if (
                not isinstance(raw_value, list)
                or len(raw_value) < 2
                or not isinstance(raw_value[1], str)
            ):
                raise LokiQueryError("Loki returned a malformed log entry")
            try:
                timestamp_ns = int(raw_value[0])
            except (TypeError, ValueError) as exc:
                raise LokiQueryError("Loki returned an invalid log timestamp") from exc
            if timestamp_ns < 0:
                raise LokiQueryError("Loki returned a negative log timestamp")
            lines.append(
                LokiLogLine(
                    timestamp_ns=timestamp_ns,
                    line=raw_value[1],
                    labels=labels,
                )
            )

    # Loki guarantees ordering inside each stream, not across streams.  Make
    # the merged result deterministic before the caller creates a cursor.
    lines.sort(
        key=lambda item: (item.timestamp_ns, tuple(sorted(item.labels.items())), item.line),
        reverse=direction == "backward",
    )
    return lines


class LokiClient:
    """Query a configured Loki endpoint without exposing its HTTP schema."""

    def __init__(
        self,
        *,
        base_url: str,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 10.0,
        client_key: str = "service-toolkit.loki",
    ) -> None:
        normalized_url = base_url.rstrip("/")
        if not normalized_url.startswith(("http://", "https://")):
            msg = "Loki base_url must be an absolute HTTP(S) URL"
            raise ValueError(msg)
        self._client = client or build_shared_async_client(
            key=client_key,
            base_url=normalized_url,
            timeout_seconds=timeout_seconds,
        )

    async def query_range(
        self,
        *,
        query: str,
        start_ns: int,
        end_ns: int,
        limit: int,
        direction: QueryDirection = "backward",
    ) -> LokiQueryResult:
        """Return at most ``limit`` logs from a finite interval.

        Time bounds are nanoseconds, matching Loki's unambiguous epoch format.
        Query construction and cursor policy stay with the domain that owns the
        caller's authorization and log identity mapping.
        """

        if not query.strip():
            msg = "Loki query must not be empty"
            raise ValueError(msg)
        _positive("start_ns", start_ns)
        _positive("end_ns", end_ns)
        _positive("limit", limit)
        if start_ns > end_ns:
            msg = "start_ns must not exceed end_ns"
            raise ValueError(msg)
        if direction not in {"forward", "backward"}:
            msg = "direction must be forward or backward"
            raise ValueError(msg)

        try:
            response = await self._client.get(
                "/loki/api/v1/query_range",
                params={
                    "query": query,
                    "start": str(start_ns),
                    "end": str(end_ns),
                    "limit": str(limit),
                    "direction": direction,
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LokiQueryError("Loki range query failed") from exc

        try:
            decoded = msgspec.json.decode(response.content)
        except msgspec.DecodeError as exc:
            raise LokiQueryError("Loki returned invalid JSON") from exc
        if not isinstance(decoded, Mapping):
            raise LokiQueryError("Loki returned a malformed response")
        return LokiQueryResult(lines=_decode_lines(decoded, direction=direction))


__all__ = ["LokiClient", "LokiLogLine", "LokiQueryError", "LokiQueryResult"]
