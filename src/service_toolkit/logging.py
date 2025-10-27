"""Logging configuration helpers."""

from __future__ import annotations

from collections.abc import Sequence
from logging import Filter, LogRecord

from litestar.logging import LoggingConfig


def build_standard_logging_config(
    *,
    suppressed_paths: Sequence[str] | None = None,
) -> LoggingConfig:
    """Return a reusable logging configuration."""

    class PathSuppressFilter(Filter):
        targets = tuple(suppressed_paths or ("GET /health",))

        def filter(self, record: LogRecord) -> bool:  # noqa: D401
            message = record.getMessage()
            return not any(target in message for target in self.targets)

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
