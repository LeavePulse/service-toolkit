"""Tests for OpenTelemetry trace-to-log correlation in service-toolkit."""

from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock

from service_toolkit.observability.logging import JsonLogFormatter, get_log_context


def test_get_log_context_extracts_otel_trace_and_span_id(monkeypatch) -> None:
    # Mock OpenTelemetry trace module
    mock_span_context = MagicMock()
    mock_span_context.is_valid = True
    mock_span_context.trace_id = 0x4BF92F3577B34DA6A3CE929D0E0E4736
    mock_span_context.span_id = 0x00F067AA0BA902B7

    mock_span = MagicMock()
    mock_span.get_span_context.return_value = mock_span_context

    mock_trace = MagicMock()
    mock_trace.get_current_span.return_value = mock_span

    import sys
    monkeypatch.setitem(sys.modules, "opentelemetry", MagicMock(trace=mock_trace))
    monkeypatch.setitem(sys.modules, "opentelemetry.trace", mock_trace)

    context = get_log_context()
    assert context["trace_id"] == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert context["span_id"] == "00f067aa0ba902b7"


def test_json_log_formatter_emits_trace_and_span_ids() -> None:
    formatter = JsonLogFormatter()
    record = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname="test.py",
        lineno=10,
        msg="Processing test request",
        args=(),
        exc_info=None,
    )
    record.request_id = "req-123"
    record.trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
    record.span_id = "00f067aa0ba902b7"
    record.user_id = "user-42"

    output = formatter.format(record)
    parsed = json.loads(output)

    assert parsed["message"] == "Processing test request"
    assert parsed["request_id"] == "req-123"
    assert parsed["trace_id"] == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert parsed["span_id"] == "00f067aa0ba902b7"
    assert parsed["user_id"] == "user-42"
    assert parsed["severity"] == "INFO"
