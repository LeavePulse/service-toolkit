from __future__ import annotations

import json
import logging

import pytest

from service_toolkit.observability.logging import (
    JsonLogFormatter,
    build_standard_logging_config,
)


def test_json_formatter_preserves_correlation_and_exception() -> None:
    record = logging.LogRecord(
        name="fleet.deploy",
        level=logging.ERROR,
        pathname=__file__,
        lineno=12,
        msg="deploy failed for %s",
        args=("service-a",),
        exc_info=None,
    )
    record.request_id = "request-1"
    record.trace_id = "a" * 32
    record.span_id = "b" * 16
    record.user_id = "operator-1"

    payload = json.loads(JsonLogFormatter().format(record))

    assert payload["message"] == "deploy failed for service-a"
    assert payload["severity"] == "ERROR"
    assert payload["trace_id"] == "a" * 32
    assert payload["span_id"] == "b" * 16
    assert payload["request_id"] == "request-1"


def test_json_logging_config_uses_json_formatter() -> None:
    config = build_standard_logging_config(log_format="json")

    assert config.formatters["standard"]["()"] is JsonLogFormatter


def test_logging_config_rejects_unknown_format() -> None:
    with pytest.raises(ValueError, match="log_format"):
        build_standard_logging_config(log_format="pretty")  # type: ignore[arg-type]
