"""Phase 21.1 supervisor quality aggregator.

Incremental upsert of turn metrics into ``quality_daily`` (never scans
``messages.metadata_json``). Negative feedback is applied to the same
bucket. Supervisor listing uses keyset cursor pagination.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.database import Database, utc_now
from app.pagination import (
    InvalidCursorError,
    _decode_cursor_payload,
    _encode_cursor_payload,
)

logger = logging.getLogger(__name__)


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def normalize_intent(value: str | None) -> str:
    cleaned = (value or "").strip()[:80]
    return cleaned or "unknown"


def normalize_prompt_version(value: str | None) -> str:
    cleaned = (value or "").strip()[:80]
    return cleaned or "default"


def encode_quality_cursor(date: str, intent: str, prompt_version: str) -> str:
    return _encode_cursor_payload({"v": 1, "d": date, "i": intent, "p": prompt_version})


def decode_quality_cursor(value: str) -> tuple[str, str, str]:
    payload = _decode_cursor_payload(value, label="quality cursor")
    date = payload.get("d")
    intent = payload.get("i")
    prompt_version = payload.get("p")
    if payload.get("v") != 1 or not isinstance(date, str) or not date.startswith("20"):
        raise InvalidCursorError("Invalid quality cursor")
    if not isinstance(intent, str) or not intent or len(intent) > 80:
        raise InvalidCursorError("Invalid quality cursor")
    if not isinstance(prompt_version, str) or not prompt_version or len(prompt_version) > 80:
        raise InvalidCursorError("Invalid quality cursor")
    return date, intent, prompt_version


@dataclass(frozen=True)
class QualityConfig:
    default_limit: int = 50
    max_limit: int = 200


class QualityService:
    """Incremental quality aggregates and supervisor listing."""

    def __init__(self, database: Database, *, config: QualityConfig | None = None) -> None:
        self.database = database
        self.config = config or QualityConfig()

    def record_turn(
        self,
        tenant_id: str,
        *,
        intent: str | None,
        prompt_version: str | None,
        escalated: bool,
        latency_ms: int,
        first_response_seconds: float | None,
        estimated_tokens: int,
        date_str: str | None = None,
    ) -> None:
        day = date_str or utc_now()[:10]
        intent_key = normalize_intent(intent)
        version_key = normalize_prompt_version(prompt_version)
        escalation = 1 if escalated else 0
        sample = 1 if first_response_seconds is not None else 0
        first_sum = float(first_response_seconds or 0.0)
        latency = max(0, int(latency_ms))
        tokens = max(0, int(estimated_tokens))
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO quality_daily (
                    tenant_id, date, intent, prompt_version,
                    turn_count, escalation_count, negative_feedback_count,
                    first_response_sum_seconds, first_response_samples,
                    latency_sum_ms, estimated_tokens
                ) VALUES (?, ?, ?, ?, 1, ?, 0, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, date, intent, prompt_version) DO UPDATE SET
                    turn_count = turn_count + 1,
                    escalation_count = escalation_count + excluded.escalation_count,
                    first_response_sum_seconds = first_response_sum_seconds
                        + excluded.first_response_sum_seconds,
                    first_response_samples = first_response_samples
                        + excluded.first_response_samples,
                    latency_sum_ms = latency_sum_ms + excluded.latency_sum_ms,
                    estimated_tokens = estimated_tokens + excluded.estimated_tokens
                """,
                (
                    tenant_id,
                    day,
                    intent_key,
                    version_key,
                    escalation,
                    first_sum,
                    sample,
                    latency,
                    tokens,
                ),
            )

    def record_negative_feedback(
        self,
        tenant_id: str,
        *,
        intent: str | None,
        prompt_version: str | None,
        date_str: str | None = None,
        delta: int = 1,
    ) -> None:
        if delta == 0:
            return
        day = date_str or utc_now()[:10]
        intent_key = normalize_intent(intent)
        version_key = normalize_prompt_version(prompt_version)
        with self.database.connect() as connection:
            if delta < 0:
                # Decrement only, never create a bucket: flipping a rating
                # back to positive must not leave a phantom turn_count=0 row.
                connection.execute(
                    """UPDATE quality_daily
                    SET negative_feedback_count =
                        MAX(0, negative_feedback_count + ?)
                    WHERE tenant_id = ? AND date = ? AND intent = ?
                      AND prompt_version = ?""",
                    (delta, tenant_id, day, intent_key, version_key),
                )
                return
            connection.execute(
                """
                INSERT INTO quality_daily (
                    tenant_id, date, intent, prompt_version,
                    turn_count, escalation_count, negative_feedback_count,
                    first_response_sum_seconds, first_response_samples,
                    latency_sum_ms, estimated_tokens
                ) VALUES (?, ?, ?, ?, 0, 0, ?, 0, 0, 0, 0)
                ON CONFLICT(tenant_id, date, intent, prompt_version) DO UPDATE SET
                    negative_feedback_count =
                        negative_feedback_count + excluded.negative_feedback_count
                """,
                (tenant_id, day, intent_key, version_key, delta),
            )

    def apply_feedback_rating(
        self,
        tenant_id: str,
        *,
        message_id: str,
        actor: str,
        new_rating: int,
        previous_rating: int | None,
        intent: str | None,
        prompt_version: str | None,
        date_str: str | None,
    ) -> None:
        """Update the quality aggregate when a feedback rating changes.

        ``previous_rating`` must be the rating stored *before* this update, so
        the caller is responsible for reading it before persisting the new
        value. A transition *to* -1 increments the negative-feedback count; a
        transition *from* -1 back to +1 decrements it.
        """
        if new_rating == -1 and previous_rating != -1:
            self.record_negative_feedback(
                tenant_id,
                intent=intent,
                prompt_version=prompt_version,
                date_str=date_str,
                delta=1,
            )
        elif new_rating == 1 and previous_rating == -1:
            self.record_negative_feedback(
                tenant_id,
                intent=intent,
                prompt_version=prompt_version,
                date_str=date_str,
                delta=-1,
            )

    def get_feedback_rating(self, tenant_id: str, message_id: str, actor: str) -> int | None:
        """Return the currently-stored feedback rating, or ``None``."""
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT rating FROM feedback
                WHERE tenant_id = ? AND message_id = ? AND actor = ?""",
                (tenant_id, message_id, actor),
            ).fetchone()
        return int(row["rating"]) if row else None

    def list_buckets(
        self,
        tenant_id: str,
        *,
        since: str | None = None,
        until: str | None = None,
        intent: str | None = None,
        prompt_version: str | None = None,
        cursor: tuple[str, str, str] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        since_date = _require_date(since, label="since")
        until_date = _require_date(until, label="until")
        page_size = min(self.config.max_limit, max(1, limit or self.config.default_limit))
        clauses = ["tenant_id = ?"]
        values: list[Any] = [tenant_id]
        if since_date is not None:
            clauses.append("date >= ?")
            values.append(since_date)
        if until_date is not None:
            clauses.append("date <= ?")
            values.append(until_date)
        if intent:
            clauses.append("intent = ?")
            values.append(normalize_intent(intent))
        if prompt_version:
            clauses.append("prompt_version = ?")
            values.append(normalize_prompt_version(prompt_version))
        if cursor is not None:
            cursor_date, cursor_intent, cursor_version = cursor
            clauses.append(
                "(date < ? OR (date = ? AND (intent > ? OR (intent = ? AND prompt_version > ?))))"
            )
            values.extend([cursor_date, cursor_date, cursor_intent, cursor_intent, cursor_version])
        query = (
            "SELECT date, intent, prompt_version, turn_count, escalation_count, "
            "negative_feedback_count, first_response_sum_seconds, "
            "first_response_samples, latency_sum_ms, estimated_tokens "
            f"FROM quality_daily WHERE {' AND '.join(clauses)} "
            "ORDER BY date DESC, intent ASC, prompt_version ASC LIMIT ?"
        )
        values.append(page_size)
        with self.database.connect() as connection:
            rows = connection.execute(query, values).fetchall()
        return [_shape_bucket(row) for row in rows]


def _shape_bucket(row: Any) -> dict[str, Any]:
    turn_count = int(row["turn_count"] or 0)
    escalation_count = int(row["escalation_count"] or 0)
    negative_count = int(row["negative_feedback_count"] or 0)
    samples = int(row["first_response_samples"] or 0)
    first_sum = float(row["first_response_sum_seconds"] or 0.0)
    latency_sum = int(row["latency_sum_ms"] or 0)
    return {
        "date": row["date"],
        "intent": row["intent"],
        "prompt_version": row["prompt_version"],
        "turn_count": turn_count,
        "escalation_count": escalation_count,
        "negative_feedback_count": negative_count,
        "escalation_rate": (escalation_count / turn_count) if turn_count else 0.0,
        "negative_feedback_rate": (negative_count / turn_count) if turn_count else 0.0,
        "avg_first_response_seconds": (first_sum / samples) if samples else 0.0,
        "avg_latency_ms": (latency_sum / turn_count) if turn_count else 0.0,
        "estimated_tokens": int(row["estimated_tokens"] or 0),
    }


def _require_date(value: str | None, *, label: str) -> str | None:
    if value is None or value == "":
        return None
    if not value.startswith("20"):
        raise ValueError(f"{label} must be YYYY-MM-DD")
    return value


__all__ = [
    "QualityConfig",
    "QualityService",
    "decode_quality_cursor",
    "encode_quality_cursor",
    "estimate_tokens",
    "normalize_intent",
    "normalize_prompt_version",
]
