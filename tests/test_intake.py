import os
from unittest import mock
from unittest.mock import Mock

from app.config import Settings
from app.intake import backpressure_reason


def test_no_backpressure_when_under_limits():
    db = Mock()
    db.turn_job_stats.side_effect = [
        {"queued": 50, "processing": 10},
        {"queued": 5, "processing": 2},
    ]
    with mock.patch.dict(os.environ, {
        "QUEUE_DEPTH_THRESHOLD": "1000",
        "TENANT_CONCURRENT_TURN_CAP": "20",
    }):
        settings = Settings.from_env()
    result = backpressure_reason(db, settings, "tenant-123")
    assert result is None


def test_backpressure_when_global_queue_overloaded():
    db = Mock()
    db.turn_job_stats.return_value = {"queued": 1500, "processing": 100}
    with mock.patch.dict(os.environ, {
        "QUEUE_DEPTH_THRESHOLD": "1000",
        "TENANT_CONCURRENT_TURN_CAP": "20",
    }):
        settings = Settings.from_env()
    result = backpressure_reason(db, settings, "tenant-123")
    assert result == "Queue overloaded (1500 queued)"


def test_backpressure_when_tenant_cap_exceeded():
    db = Mock()
    db.turn_job_stats.side_effect = [
        {"queued": 50, "processing": 10},
        {"queued": 15, "processing": 10},
    ]
    with mock.patch.dict(os.environ, {
        "QUEUE_DEPTH_THRESHOLD": "1000",
        "TENANT_CONCURRENT_TURN_CAP": "20",
    }):
        settings = Settings.from_env()
    result = backpressure_reason(db, settings, "tenant-123")
    assert result == "Tenant turn cap exceeded (25)"


def test_backpressure_exactly_at_global_threshold():
    db = Mock()
    db.turn_job_stats.side_effect = [
        {"queued": 1000, "processing": 0},
        {"queued": 5, "processing": 2},
    ]
    with mock.patch.dict(os.environ, {
        "QUEUE_DEPTH_THRESHOLD": "1000",
        "TENANT_CONCURRENT_TURN_CAP": "20",
    }):
        settings = Settings.from_env()
    result = backpressure_reason(db, settings, "tenant-123")
    assert result is None


def test_backpressure_exactly_at_tenant_cap():
    db = Mock()
    db.turn_job_stats.side_effect = [
        {"queued": 50, "processing": 10},
        {"queued": 10, "processing": 10},
    ]
    with mock.patch.dict(os.environ, {
        "QUEUE_DEPTH_THRESHOLD": "1000",
        "TENANT_CONCURRENT_TURN_CAP": "20",
    }):
        settings = Settings.from_env()
    result = backpressure_reason(db, settings, "tenant-123")
    assert result is None


def test_backpressure_global_check_first():
    db = Mock()
    db.turn_job_stats.return_value = {"queued": 2000, "processing": 0}
    with mock.patch.dict(os.environ, {
        "QUEUE_DEPTH_THRESHOLD": "1000",
        "TENANT_CONCURRENT_TURN_CAP": "5",
    }):
        settings = Settings.from_env()
    result = backpressure_reason(db, settings, "tenant-123")
    assert result == "Queue overloaded (2000 queued)"
    assert db.turn_job_stats.call_count == 1
