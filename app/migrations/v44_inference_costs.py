"""ROADMAP 2.3.x: AI inference cost attribution (expand phase).

Adds per-inference cost records and daily tenant/provider/model aggregates so
the operator can attribute spend to tenants, agents/prompts, and conversations:

- ``inference_costs`` — one row per model inference with vendor-reported token
  usage and the computed USD cost (``cost_usd`` is NULL when no pricing is
  declared — never guessed);
- ``tenant_cost_daily`` — incremental daily rollup upserted by the
  attribution service, the backing store for the cost dashboard API.

This is expand-only: no existing table is modified, and attribution is opt-in
(rows are only written when a model provider is configured).
"""

from __future__ import annotations

import sqlite3

from app.migrations import migration


@migration(
    44,
    "Inference cost attribution: per-inference costs and daily aggregates (ROADMAP 2.3.x)",
    phase="expand",
)
def migrate(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS inference_costs (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL REFERENCES tenants(id),
            conversation_id TEXT,
            turn_id TEXT,
            message_id TEXT,
            agent TEXT,
            prompt_version TEXT,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            prompt_tokens INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            cost_usd REAL,
            latency_ms INTEGER,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_inference_costs_tenant_created "
        "ON inference_costs(tenant_id, created_at)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_inference_costs_conversation "
        "ON inference_costs(conversation_id, created_at)"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS tenant_cost_daily (
            tenant_id TEXT NOT NULL,
            date TEXT NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            turn_count INTEGER NOT NULL DEFAULT 0,
            prompt_tokens INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            cost_usd REAL,
            PRIMARY KEY (tenant_id, date, provider, model)
        )
        """
    )
