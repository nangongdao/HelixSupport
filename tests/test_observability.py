"""Tests for app/observability.py logging and metrics module."""

import json
import logging
import os
from unittest import mock

from app.observability import JsonFormatter, RuntimeMetrics, configure_logging


def test_json_formatter_basic_record():
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="Test message",
        args=(),
        exc_info=None,
    )
    output = formatter.format(record)
    parsed = json.loads(output)
    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "test.logger"
    assert parsed["message"] == "Test message"
    assert "timestamp" in parsed


def test_json_formatter_with_request_fields():
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="helix",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="Request completed",
        args=(),
        exc_info=None,
    )
    record.request_id = "req_123"
    record.method = "GET"
    record.route = "/api/health"
    record.status_code = 200
    record.duration_ms = 15
    output = formatter.format(record)
    parsed = json.loads(output)
    assert parsed["request_id"] == "req_123"
    assert parsed["method"] == "GET"
    assert parsed["route"] == "/api/health"
    assert parsed["status_code"] == 200
    assert parsed["duration_ms"] == 15


def test_json_formatter_with_exception():
    formatter = JsonFormatter()
    try:
        raise ValueError("Test error")
    except ValueError:
        import sys

        exc_info = sys.exc_info()
        record = logging.LogRecord(
            name="helix",
            level=logging.ERROR,
            pathname="",
            lineno=0,
            msg="Error occurred",
            args=(),
            exc_info=exc_info,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert "exception" in parsed
        assert "ValueError" in parsed["exception"]
        assert "Test error" in parsed["exception"]


def test_configure_logging_creates_handler():
    logger = logging.getLogger("helix")
    original_handlers = logger.handlers[:]
    logger.handlers.clear()

    configure_logging()

    assert len(logger.handlers) == 1
    assert isinstance(logger.handlers[0].formatter, JsonFormatter)
    assert logger.level == logging.INFO
    assert logger.propagate is False

    logger.handlers = original_handlers


def test_configure_logging_skips_if_already_configured():
    logger = logging.getLogger("helix")
    original_handlers = logger.handlers[:]
    logger.handlers.clear()

    configure_logging()
    first_handler_count = len(logger.handlers)
    configure_logging()

    assert len(logger.handlers) == first_handler_count

    logger.handlers = original_handlers


def test_configure_logging_respects_log_level_env():
    logger = logging.getLogger("helix")
    original_handlers = logger.handlers[:]
    logger.handlers.clear()

    with mock.patch.dict(os.environ, {"LOG_LEVEL": "DEBUG"}):
        configure_logging()

    assert logger.level == logging.DEBUG

    logger.handlers = original_handlers


def test_runtime_metrics_observe_request():
    metrics = RuntimeMetrics()
    metrics.observe_request("/api/health", 200, 10)
    metrics.observe_request("/api/users", 201, 20)

    snapshot = metrics.snapshot()
    assert snapshot["requests_total"] == 2
    assert snapshot["server_errors_total"] == 0
    assert snapshot["average_duration_ms"] == 15.0
    assert snapshot["by_status"]["200"] == 1
    assert snapshot["by_status"]["201"] == 1
    assert snapshot["by_route"]["/api/health"] == 1
    assert snapshot["by_route"]["/api/users"] == 1


def test_runtime_metrics_tracks_server_errors():
    metrics = RuntimeMetrics()
    metrics.observe_request("/api/endpoint", 200, 10)
    metrics.observe_request("/api/endpoint", 500, 20)
    metrics.observe_request("/api/endpoint", 503, 30)

    snapshot = metrics.snapshot()
    assert snapshot["requests_total"] == 3
    assert snapshot["server_errors_total"] == 2


def test_runtime_metrics_empty_snapshot():
    metrics = RuntimeMetrics()
    snapshot = metrics.snapshot()

    assert snapshot["requests_total"] == 0
    assert snapshot["server_errors_total"] == 0
    assert snapshot["average_duration_ms"] == 0
    assert snapshot["by_status"] == {}
    assert snapshot["by_route"] == {}


def test_runtime_metrics_multiple_same_route():
    metrics = RuntimeMetrics()
    metrics.observe_request("/api/health", 200, 5)
    metrics.observe_request("/api/health", 200, 10)
    metrics.observe_request("/api/health", 200, 15)

    snapshot = metrics.snapshot()
    assert snapshot["by_route"]["/api/health"] == 3
    assert snapshot["by_status"]["200"] == 3
    assert snapshot["average_duration_ms"] == 10.0
