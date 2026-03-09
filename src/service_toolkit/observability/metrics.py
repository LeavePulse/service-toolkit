"""Shared Prometheus metrics utilities.

Provides the ``metric_label()`` sanitiser and ``ThrottledGaugeRefresh`` helper
that were duplicated across server-service, community-service and
whitelist-service business metrics modules.
"""

from __future__ import annotations

import asyncio
import time


def metric_label(
    value: object | None,
    *,
    fallback: str = "unknown",
    max_len: int = 96,
) -> str:
    """Sanitise a value for use as a Prometheus label.

    Drop-in replacement for the ``_label()`` helper duplicated in every
    service's ``business_metrics.py``.
    """
    text = str(value or "").strip().lower()
    if not text:
        return fallback
    return text[:max_len]


class ThrottledGaugeRefresh:
    """Double-checked-locking throttle for aggregate Prometheus gauge refreshes.

    Usage::

        _refresher = ThrottledGaugeRefresh(min_interval_seconds=20.0)

        async def refresh(session: AsyncSession, *, force: bool = False) -> None:
            if not _refresher.should_run(force=force):
                return
            async with _refresher.lock():
                if not _refresher.should_run(force=force):
                    return
                try:
                    # ... run DB queries and set gauges ...
                except Exception:
                    logger.exception("...")
                    return
                _refresher.mark_done()
    """

    def __init__(self, *, min_interval_seconds: float = 20.0) -> None:
        self.min_interval_seconds = min_interval_seconds
        self._lock = asyncio.Lock()
        self._last_refresh_monotonic = 0.0

    def should_run(self, *, force: bool = False) -> bool:
        """Check whether enough time has elapsed since the last refresh."""
        if force:
            return True
        return (time.monotonic() - self._last_refresh_monotonic) >= self.min_interval_seconds

    def lock(self) -> asyncio.Lock:
        """Return the internal lock for use with ``async with``."""
        return self._lock

    def mark_done(self) -> None:
        """Record that a successful refresh just completed."""
        self._last_refresh_monotonic = time.monotonic()


__all__ = ["ThrottledGaugeRefresh", "metric_label"]
