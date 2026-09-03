"""Migration 32: data field registry + DSR SLA + deletion tombstones (Phase 41.4 DATA) (Phase 41.6 / ARC-001 deep-module split)."""

from __future__ import annotations

import sqlite3

from app.migrations import _ensure_column, migration


@migration(32, "data field registry + DSR SLA + deletion tombstones (Phase 41.4 DATA)")
def migration_32(connection: sqlite3.Connection) -> None:
    _ensure_column(connection, "data_subject_requests", "sla_due_at", "TEXT")
    _ensure_column(
        connection, "data_subject_requests", "sla_breached", "INTEGER NOT NULL DEFAULT 0"
    )
    _ensure_column(connection, "data_subject_requests", "last_errored_at", "TEXT")
    _ensure_column(connection, "data_subject_requests", "error_detail", "TEXT")
    _ensure_column(connection, "data_subject_requests", "execution_secret", "TEXT")
    connection.executescript("""
            CREATE TABLE IF NOT EXISTS data_field_registry (
                field TEXT PRIMARY KEY,
                classification TEXT NOT NULL
                    CHECK (classification IN ('public', 'internal', 'confidential', 'restricted')),
                collection_purpose TEXT NOT NULL DEFAULT '',
                retention_days INTEGER,
                region TEXT NOT NULL DEFAULT 'global',
                downstream TEXT NOT NULL DEFAULT '[]'
            );
    
            CREATE TABLE IF NOT EXISTS customer_tombstones (
                tenant_id TEXT NOT NULL,
                customer_ref TEXT NOT NULL,
                request_id TEXT NOT NULL,
                deleted_at TEXT NOT NULL,
                deleted_by TEXT NOT NULL,
                secret_hash TEXT NOT NULL,
                PRIMARY KEY (tenant_id, customer_ref)
            );
            CREATE INDEX IF NOT EXISTS idx_customer_tombstones_tenant
                ON customer_tombstones(tenant_id, deleted_at);
    
            CREATE TABLE IF NOT EXISTS deferred_deletion_jobs (
                tenant_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                customer_ref TEXT NOT NULL,
                status TEXT NOT NULL
                    CHECK (status IN ('approved', 'retryable', 'completed', 'failed')),
                attempt INTEGER NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                PRIMARY KEY (tenant_id, request_id)
            );
            CREATE INDEX IF NOT EXISTS idx_deferred_deletion_jobs_status
                ON deferred_deletion_jobs(status, created_at);
            """)
