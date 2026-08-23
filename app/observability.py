from __future__ import annotations

import json
import logging
import os
import threading
from collections import Counter
from datetime import UTC, datetime
from typing import Any


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("request_id", "method", "route", "status_code", "duration_ms"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> None:
    logger = logging.getLogger("helix")
    if logger.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    level_name = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    logger.setLevel(getattr(logging, level_name, logging.INFO))
    logger.propagate = False


class RuntimeMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests = 0
        self._errors = 0
        self._duration_ms = 0
        self._by_status: Counter[str] = Counter()
        self._by_route: Counter[str] = Counter()

    def observe_request(self, route: str, status_code: int, duration_ms: int) -> None:
        with self._lock:
            self._requests += 1
            self._duration_ms += duration_ms
            self._by_status[str(status_code)] += 1
            self._by_route[route] += 1
            if status_code >= 500:
                self._errors += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            average = self._duration_ms / self._requests if self._requests else 0
            return {
                "requests_total": self._requests,
                "server_errors_total": self._errors,
                "average_duration_ms": round(average, 2),
                "by_status": dict(self._by_status),
                "by_route": dict(self._by_route),
            }
