"""Migration 29: DSR maker-checker workflow and encrypted exports (M0 SEC-002) (Phase 41.6 / ARC-001 deep-module split)."""

from __future__ import annotations

import sqlite3

from app.migrations import _ensure_column, migration


@migration(29, "DSR maker-checker workflow and encrypted exports (M0 SEC-002)")
def migration_29(connection: sqlite3.Connection) -> None:
    _ensure_column(connection, "data_subject_requests", "idempotency_key", "TEXT")
    _ensure_column(connection, "data_subject_requests", "approver", "TEXT")
    _ensure_column(connection, "data_subject_requests", "approved_at", "TEXT")
    _ensure_column(connection, "data_subject_requests", "executor", "TEXT")
    _ensure_column(connection, "data_subject_requests", "executed_at", "TEXT")
    _ensure_column(connection, "data_subject_requests", "execution_summary_json", "TEXT")
    _ensure_column(connection, "data_subject_requests", "export_object_id", "TEXT")
    connection.executescript("""
            CREATE TABLE IF NOT EXISTS dsr_export_objects (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL REFERENCES tenants(id),
                request_id TEXT NOT NULL,
                encrypted_blob TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                download_token_hash TEXT,
                download_token_expires_at TEXT,
                downloaded_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_dsr_export_expiry
                ON dsr_export_objects(expires_at);
            CREATE INDEX IF NOT EXISTS idx_dsr_export_token
                ON dsr_export_objects(download_token_hash);
            """)
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "data_subject_requests" not in tables:
        return
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_dsr_request_idempotency ON data_subject_requests(tenant_id, idempotency_key) WHERE idempotency_key IS NOT NULL"
    )
    return
    return
