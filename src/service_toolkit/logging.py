"""Logging configuration helpers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from contextvars import ContextVar, Token
from logging import Filter, LogRecord
from typing import Any
from uuid import uuid4

from litestar.logging import LoggingConfig
from litestar.types import ASGIApp

DEFAULT_SUPPRESSED_PATTERNS: tuple[str, ...] = (
    "GET /health",
    "HEAD /health",
    "GET /metrics",
)

_REQUEST_ID_VAR: ContextVar[str] = ContextVar("leavepulse_request_id", default="-")
_TRACE_ID_VAR: ContextVar[str] = ContextVar("leavepulse_trace_id", default="-")
_USER_ID_VAR: ContextVar[str] = ContextVar("leavepulse_user_id", default="-")

_MAX_CONTEXT_VALUE_LEN = 128
Scope = dict[str, object]
Receive = Callable[[], Awaitable[dict[str, object]]]
Send = Callable[[dict[str, object]], Awaitable[None]]


def _context_label(
    value: object | None,
    *,
    fallback: str = "-",
    max_len: int = _MAX_CONTEXT_VALUE_LEN,
) -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    return text[:max_len]


def _extract_header(scope: Scope, name: str) -> str | None:
    target = name.lower().encode()
    for raw_name, raw_value in scope.get("headers", []):
        if raw_name.lower() != target:
            continue
        return raw_value.decode("utf-8", errors="ignore").strip() or None
    return None


def _extract_trace_id(scope: Scope) -> str | None:
    traceparent = _extract_header(scope, "traceparent")
    if traceparent:
        parts = traceparent.split("-")
        if len(parts) >= 4:
            trace_id = parts[1].strip().lower()
            if len(trace_id) == 32:
                return trace_id
    return _extract_header(scope, "x-trace-id")


def _current_otel_trace_id() -> str | None:
    try:
        from opentelemetry import trace
    except ModuleNotFoundError:
        return None

    span = trace.get_current_span()
    if span is None:
        return None
    span_context = span.get_span_context()
    if span_context is None or not span_context.is_valid:
        return None
    return f"{int(span_context.trace_id):032x}"


def bind_log_user_id(user_id: object | None) -> None:
    """Bind authenticated user identifier into request-local logging context."""
    _USER_ID_VAR.set(_context_label(user_id))


def get_log_context() -> dict[str, str]:
    """Return currently active logging context values."""
    trace_id = _TRACE_ID_VAR.get()
    if trace_id == "-":
        trace_id = _current_otel_trace_id() or trace_id
    return {
        "request_id": _REQUEST_ID_VAR.get(),
        "trace_id": trace_id,
        "user_id": _USER_ID_VAR.get(),
    }


def _bind_log_context(
    *,
    request_id: object | None,
    trace_id: object | None,
    user_id: object | None,
) -> tuple[Token[str], Token[str], Token[str]]:
    request_token = _REQUEST_ID_VAR.set(_context_label(request_id))
    trace_token = _TRACE_ID_VAR.set(_context_label(trace_id))
    user_token = _USER_ID_VAR.set(_context_label(user_id))
    return request_token, trace_token, user_token


def _reset_log_context(tokens: tuple[Token[str], Token[str], Token[str]]) -> None:
    request_token, trace_token, user_token = tokens
    _REQUEST_ID_VAR.reset(request_token)
    _TRACE_ID_VAR.reset(trace_token)
    _USER_ID_VAR.reset(user_token)


class RequestContextLoggingMiddleware:
    """ASGI middleware that sets request-scoped logging context values."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        request_id = _extract_header(scope, "x-request-id") or uuid4().hex
        trace_id = _extract_trace_id(scope) or request_id
        user_id = _extract_header(scope, "x-user-id")
        tokens = _bind_log_context(
            request_id=request_id,
            trace_id=trace_id,
            user_id=user_id,
        )

        async def send_wrapper(message: dict[str, object]) -> None:
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers") or [])
                if not any(name.lower() == b"x-request-id" for name, _ in headers):
                    headers.append((b"x-request-id", request_id.encode("utf-8")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            _reset_log_context(tokens)


def request_context_middleware(app: ASGIApp) -> ASGIApp:
    """Return request context middleware as an ASGI app factory."""
    return RequestContextLoggingMiddleware(app)


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

    class ContextFilter(Filter):
        def filter(self, record: LogRecord) -> bool:  # noqa: D401
            context = get_log_context()
            record.request_id = context["request_id"]
            record.trace_id = context["trace_id"]
            record.user_id = context["user_id"]
            return True

    class PathSuppressFilter(Filter):
        active_patterns = patterns

        def filter(self, record: LogRecord) -> bool:  # noqa: D401
            message = record.getMessage()
            return not any(pattern in message for pattern in self.active_patterns)

    return LoggingConfig(
        formatters={
            "standard": {
                "format": (
                    "%(levelname)s [req=%(request_id)s trace=%(trace_id)s "
                    "user=%(user_id)s] %(message)s"
                )
            },
        },
        handlers={
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "standard",
                "filters": ["context_filter"],
            },
        },
        root={
            "level": "INFO",
            "handlers": ["console"],
        },
        loggers={
            "uvicorn.access": {
                "level": "INFO",
                "handlers": ["console"],
                "propagate": False,
                "filters": ["context_filter", "path_suppress_filter"],
            },
        },
        filters={
            "context_filter": {"()": ContextFilter},
            "path_suppress_filter": {"()": PathSuppressFilter},
        },
    )


__all__ = [
    "RequestContextLoggingMiddleware",
    "request_context_middleware",
    "bind_log_user_id",
    "build_standard_logging_config",
    "get_log_context",
]
