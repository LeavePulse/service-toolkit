"""Logging configuration helpers."""

from __future__ import annotations

from collections.abc import Sequence
from logging import Filter, LogRecord

from litestar.logging import LoggingConfig

DEFAULT_SUPPRESSED_PATTERNS: tuple[str, ...] = (
    "GET /health",
    "HEAD /health",
    "GET /metrics",
)


def _merge_patterns(
    suppressed_paths: Sequence[str] | None,
    include_default: bool,
) -> tuple[str, ...]:
    patterns: list[str] = []
    if include_default:
        patterns.extend(DEFAULT_SUPPRESSED_PATTERNS)
    if suppressed_paths:
        patterns.extend(suppressed_paths)
    return tuple(patterns)


def build_standard_logging_config(
    *,
    suppressed_paths: Sequence[str] | None = None,
    include_default_suppressed: bool = True,
) -> LoggingConfig:
    """Return a reusable logging configuration.

    Args:
        suppressed_paths: Additional path fragments to filter from the access log.
            Pass an empty tuple to disable all suppression.
        include_default_suppressed: Include built-in `/health` and `/metrics` filters.
    """

    patterns = _merge_patterns(suppressed_paths, include_default_suppressed)

    class PathSuppressFilter(Filter):
        active_patterns = patterns

        def filter(self, record: LogRecord) -> bool:  # noqa: D401
            message = record.getMessage()
            return not any(pattern in message for pattern in self.active_patterns)

    return LoggingConfig(
        formatters={
            "standard": {"format": "%(levelname)s: %(message)s"},
        },
        handlers={
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "standard",
            },
        },
        loggers={
            "uvicorn.access": {
                "level": "INFO",
                "handlers": ["console"],
                "propagate": False,
                "filters": ["path_suppress_filter"],
            },
        },
        filters={
            "path_suppress_filter": {"()": PathSuppressFilter},
        },
    )


__all__ = ["build_standard_logging_config"]
