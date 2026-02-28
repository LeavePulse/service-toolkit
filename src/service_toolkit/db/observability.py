"""SQLAlchemy observability helpers (slow-query logging)."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Connection, Engine

_SLOW_QUERY_STACK_KEY = "leavepulse_slow_query_start_stack"
_install_lock = threading.Lock()
_installed = False


def _normalize_text(value: object | None, *, max_len: int = 600) -> str:
    text = str(value or "").strip()
    if not text:
        return "-"
    compact = " ".join(text.split())
    if len(compact) > max_len:
        return f"{compact[:max_len]}..."
    return compact


def install_slow_query_logging(
    *,
    service_name: str,
    threshold_seconds: float = 0.25,
    include_parameters: bool = False,
) -> None:
    """Install process-wide SQLAlchemy event listeners for slow-query logging."""
    global _installed

    if threshold_seconds <= 0:
        return

    with _install_lock:
        if _installed:
            return

        logger = logging.getLogger(f"{service_name}.db.slow")

        @event.listens_for(Engine, "before_cursor_execute")
        def _before_cursor_execute(
            conn: Connection,
            _cursor: Any,
            _statement: str,
            _parameters: Any,
            _context: Any,
            _executemany: bool,
        ) -> None:
            stack = conn.info.setdefault(_SLOW_QUERY_STACK_KEY, [])
            stack.append(time.perf_counter())

        @event.listens_for(Engine, "after_cursor_execute")
        def _after_cursor_execute(
            conn: Connection,
            cursor: Any,
            statement: str,
            parameters: Any,
            _context: Any,
            executemany: bool,
        ) -> None:
            stack = conn.info.get(_SLOW_QUERY_STACK_KEY)
            if not stack:
                return
            started_at = stack.pop()
            duration_seconds = time.perf_counter() - float(started_at)
            if duration_seconds < threshold_seconds:
                return

            sql = _normalize_text(statement)
            rowcount = int(getattr(cursor, "rowcount", -1) or -1)
            payload = {
                "duration_seconds": round(duration_seconds, 6),
                "rowcount": rowcount,
                "executemany": bool(executemany),
                "sql": sql,
            }
            if include_parameters:
                payload["parameters"] = _normalize_text(parameters)

            logger.warning("Slow SQL query detected", extra=payload)

        _installed = True


__all__ = ["install_slow_query_logging"]
