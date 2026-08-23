"""Scheduled report generation and export (backlog: 报表导出与订阅).

Quality and usage reports are generated over a bounded lookback window
(default 7 days, max 30) from the existing aggregates — quality buckets per
day/intent/prompt version and per-tenant daily usage. A subscription pairs a
report type with a cadence (daily/weekly) and an outbound webhook endpoint;
the worker scan runs due subscriptions and delivers each report through the
webhook machinery, deduplicated per subscription + period so a missed scan is
picked up without double-delivery. On-demand generation and CSV export are
available through the admin API.
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import date, timedelta
from typing import Any
from uuid import uuid4

from app.db._util import utc_now
from app.webhooks import EVENT_REPORT_GENERATED

logger = logging.getLogger("helix")

REPORT_TYPES = ("quality", "usage")
SCHEDULES = ("daily", "weekly")
MAX_WINDOW_DAYS = 30
DEFAULT_WINDOW_DAYS = 7

# CSV columns per report type (the source rows are dicts from the
# aggregations; only these keys are exported, in this order).
_QUALITY_COLUMNS = (
    "date",
    "intent",
    "prompt_version",
    "turn_count",
    "escalation_count",
    "negative_feedback_count",
    "escalation_rate",
    "negative_feedback_rate",
    "avg_first_response_seconds",
    "avg_latency_ms",
    "estimated_tokens",
)
_USAGE_COLUMNS = (
    "date",
    "tenant_id",
    "turn_count",
    "conversation_count",
    "message_count",
)


class ReportService:
    """Generates reports, exports CSV, and runs due report subscriptions."""

    def __init__(
        self,
        database: Any,
        quality_service: Any,
        webhook_service: Any,
    ) -> None:
        self.database = database
        self.quality = quality_service
        self.webhooks = webhook_service

    # -------------------------------------------------------------- generation

    def generate(
        self, tenant_id: str, report_type: str, window_days: int = DEFAULT_WINDOW_DAYS
    ) -> dict[str, Any]:
        """Build a report over ``[today-window+1, today]`` (inclusive)."""
        if report_type not in REPORT_TYPES:
            raise ValueError(f"report_type must be one of {REPORT_TYPES}")
        window = max(1, min(int(window_days), MAX_WINDOW_DAYS))
        today = utc_now()[:10]
        since = (date.fromisoformat(today) - timedelta(days=window - 1)).isoformat()
        if report_type == "quality":
            rows = self.quality.list_buckets(tenant_id, since=since, until=today)
        else:
            rows = self.database.list_tenant_usage(tenant_id, since=since, until=today)
        return {
            "report_type": report_type,
            "from_date": since,
            "to_date": today,
            "window_days": window,
            "rows": rows,
            "generated_at": utc_now(),
        }

    # ------------------------------------------------------------------ export

    def rows_for_range(
        self,
        tenant_id: str,
        report_type: str,
        from_date: str | None,
        to_date: str | None,
    ) -> list[dict[str, Any]]:
        """Rows over an explicit date range (CSV export path)."""
        if report_type not in REPORT_TYPES:
            raise ValueError(f"report_type must be one of {REPORT_TYPES}")
        if report_type == "quality":
            return self.quality.list_buckets(tenant_id, since=from_date, until=to_date)
        return self.database.list_tenant_usage(tenant_id, since=from_date, until=to_date)

    def export_csv(self, report_type: str, rows: list[dict[str, Any]]) -> str:
        columns = _QUALITY_COLUMNS if report_type == "quality" else _USAGE_COLUMNS
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in columns})
        return buffer.getvalue()

    # ------------------------------------------------------------ subscriptions

    def create_subscription(
        self,
        tenant_id: str,
        *,
        report_type: str,
        schedule: str,
        webhook_endpoint_id: str,
        window_days: int = DEFAULT_WINDOW_DAYS,
        actor_id: str,
    ) -> dict[str, Any]:
        if report_type not in REPORT_TYPES:
            raise ValueError(f"report_type must be one of {REPORT_TYPES}")
        if schedule not in SCHEDULES:
            raise ValueError(f"schedule must be one of {SCHEDULES}")
        window = max(1, min(int(window_days), MAX_WINDOW_DAYS))
        with self.database.connect() as conn:
            endpoint = conn.execute(
                "SELECT id FROM webhook_endpoints WHERE tenant_id = ? AND id = ? "
                "AND status = 'active'",
                (tenant_id, webhook_endpoint_id),
            ).fetchone()
            if endpoint is None:
                raise LookupError("Webhook endpoint not found")
            subscription_id = f"sub_{uuid4().hex[:12]}"
            now = utc_now()
            conn.execute(
                """INSERT INTO report_subscriptions
                (id, tenant_id, report_type, schedule, window_days, webhook_endpoint_id,
                 active, created_by, created_at, updated_at, last_run_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, NULL)""",
                (
                    subscription_id,
                    tenant_id,
                    report_type,
                    schedule,
                    window,
                    webhook_endpoint_id,
                    actor_id,
                    now,
                    now,
                ),
            )
        return self.get_subscription(tenant_id, subscription_id) or {}

    def get_subscription(self, tenant_id: str, subscription_id: str) -> dict[str, Any] | None:
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT * FROM report_subscriptions WHERE tenant_id = ? AND id = ?",
                (tenant_id, subscription_id),
            ).fetchone()
        return dict(row) if row else None

    def list_subscriptions(self, tenant_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM report_subscriptions WHERE tenant_id = ? ORDER BY created_at DESC",
                (tenant_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_subscription(
        self,
        tenant_id: str,
        subscription_id: str,
        fields: dict[str, Any],
        actor_id: str,
    ) -> dict[str, Any]:
        allowed = {"active", "schedule", "window_days"}
        changes = {key: value for key, value in fields.items() if key in allowed}
        if "schedule" in changes and changes["schedule"] not in SCHEDULES:
            raise ValueError(f"schedule must be one of {SCHEDULES}")
        if "window_days" in changes:
            changes["window_days"] = max(1, min(int(changes["window_days"]), MAX_WINDOW_DAYS))
        if "active" in changes:
            changes["active"] = 1 if changes["active"] else 0
        if changes:
            changes["updated_at"] = utc_now()
            assignments = ", ".join(f"{key} = ?" for key in changes)
            with self.database.connect() as conn:
                cursor = conn.execute(
                    f"""UPDATE report_subscriptions SET {assignments}
                    WHERE tenant_id = ? AND id = ?""",
                    [*changes.values(), tenant_id, subscription_id],
                )
                if cursor.rowcount != 1:
                    raise LookupError("Report subscription not found")
        self.database.audit(
            tenant_id,
            None,
            actor_id,
            "report.subscription_updated",
            {"subscription_id": subscription_id, "fields": sorted(changes)},
        )
        return self.get_subscription(tenant_id, subscription_id) or {}

    def delete_subscription(self, tenant_id: str, subscription_id: str, actor_id: str) -> bool:
        with self.database.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM report_subscriptions WHERE tenant_id = ? AND id = ?",
                (tenant_id, subscription_id),
            )
        if cursor.rowcount != 1:
            return False
        self.database.audit(
            tenant_id,
            None,
            actor_id,
            "report.subscription_deleted",
            {"subscription_id": subscription_id},
        )
        return True

    # ---------------------------------------------------------------- delivery

    def deliver_report(
        self,
        tenant_id: str,
        endpoint_id: str,
        report: dict[str, Any],
        dedup_key: str,
    ) -> int:
        """Enqueue the report to one endpoint (idempotent per ``dedup_key``)."""
        payload = {
            "report_type": report["report_type"],
            "from_date": report["from_date"],
            "to_date": report["to_date"],
            "window_days": report["window_days"],
            "generated_at": report["generated_at"],
            "rows": report["rows"],
        }
        return self.webhooks.emit_event_to_endpoint(
            tenant_id,
            endpoint_id,
            EVENT_REPORT_GENERATED,
            payload,
            event_id=f"report:{dedup_key}:{report['to_date']}",
        )

    # ------------------------------------------------------------------- scan

    def run_due_subscriptions(self) -> int:
        """Generate and deliver every due subscription; returns processed count.

        Best-effort per subscription: a failure is logged and the scan moves
        on, so one bad endpoint never blocks the rest.
        """
        today = utc_now()[:10]
        with self.database.connect() as conn:
            rows = conn.execute("SELECT * FROM report_subscriptions WHERE active = 1").fetchall()
        processed = 0
        for row in rows:
            subscription = dict(row)
            if not self._due(subscription, today):
                continue
            try:
                report = self.generate(
                    subscription["tenant_id"],
                    subscription["report_type"],
                    subscription["window_days"],
                )
                self.deliver_report(
                    subscription["tenant_id"],
                    subscription["webhook_endpoint_id"],
                    report,
                    dedup_key=subscription["id"],
                )
            except Exception:
                logger.exception(
                    "report.subscription_failed",
                    extra={"subscription_id": subscription["id"]},
                )
                continue
            with self.database.connect() as conn:
                conn.execute(
                    "UPDATE report_subscriptions SET last_run_at = ? WHERE id = ?",
                    (utc_now(), subscription["id"]),
                )
            processed += 1
        return processed

    @staticmethod
    def _due(subscription: dict[str, Any], today: str) -> bool:
        last_run = subscription.get("last_run_at")
        if not last_run:
            return True
        try:
            last_date = date.fromisoformat(last_run[:10])
        except ValueError:
            return True
        interval = 1 if subscription["schedule"] == "daily" else 7
        return (date.fromisoformat(today) - last_date).days >= interval
