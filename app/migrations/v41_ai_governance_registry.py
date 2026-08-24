"""ROADMAP 43.5: AI governance registry (expand phase).

Four additive tables backing the eval registry and the online-feedback
review workflow:

- ``ai_eval_datasets``  — versioned eval datasets with a content hash, so a
  dataset referenced by an eval run is reproducible (same hash ⇒ same items).
- ``ai_eval_runs``      — one row per evaluation run, linking the dataset, the
  candidate (prompt version id or model ref) and the immutable WORM report.
- ``ai_approvals``      — maker-checker approval records for AI-subject
  changes (promotion, tool action, feedback batch); the requester cannot
  approve their own request.
- ``ai_online_feedback``— live-traffic feedback staged for training/eval use;
  rows persist only after :func:`app.redaction.redact_sensitive` and stay
  ``pending_review`` until a human reviewer accepts them into a dataset.

No existing reader or writer is touched: this is expand-only.
"""

from __future__ import annotations

import sqlite3

from app.migrations import migration


@migration(
    41,
    "AI governance registry: eval datasets/runs, approvals, online feedback review (ROADMAP 43.5)",
    phase="expand",
)
def migrate(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_eval_datasets (
            id TEXT PRIMARY KEY,
            tenant_id TEXT REFERENCES tenants(id),
            name TEXT NOT NULL,
            version INTEGER NOT NULL,
            strategy TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            item_count INTEGER NOT NULL DEFAULT 0,
            created_by TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            UNIQUE (tenant_id, name, version)
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_ai_eval_datasets_tenant "
        "ON ai_eval_datasets(tenant_id, name)"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_eval_runs (
            id TEXT PRIMARY KEY,
            dataset_id TEXT NOT NULL REFERENCES ai_eval_datasets(id),
            candidate TEXT NOT NULL,
            baseline TEXT,
            report_object_id TEXT,
            passed INTEGER,
            metrics_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_ai_eval_runs_dataset ON ai_eval_runs(dataset_id)"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_eval_dataset_items (
            dataset_id TEXT NOT NULL REFERENCES ai_eval_datasets(id),
            position INTEGER NOT NULL,
            item_json TEXT NOT NULL,
            PRIMARY KEY (dataset_id, position)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_approvals (
            id TEXT PRIMARY KEY,
            tenant_id TEXT REFERENCES tenants(id),
            subject_kind TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            requested_by TEXT NOT NULL,
            decided_by TEXT,
            decision TEXT NOT NULL DEFAULT 'pending',
            reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            decided_at TEXT
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_ai_approvals_subject "
        "ON ai_approvals(subject_kind, subject_id)"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_online_feedback (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL REFERENCES tenants(id),
            conversation_id TEXT,
            source TEXT NOT NULL,
            redacted_json TEXT NOT NULL,
            review_status TEXT NOT NULL DEFAULT 'pending_review',
            reviewed_by TEXT,
            reviewed_at TEXT,
            dataset_id TEXT REFERENCES ai_eval_datasets(id),
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_ai_feedback_queue "
        "ON ai_online_feedback(tenant_id, review_status)"
    )
