"""Migration 15: auto routing rules and agent groups (Phase 41.6 / ARC-001 deep-module split)."""

from __future__ import annotations

import sqlite3

from app.migrations import migration


@migration(15, "auto routing rules and agent groups")
def migration_15(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS agent_groups (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            name TEXT NOT NULL,
            skills_json TEXT NOT NULL DEFAULT '[]',
            capacity INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            UNIQUE (tenant_id, name)
        );
        CREATE INDEX IF NOT EXISTS idx_agent_groups_tenant
            ON agent_groups(tenant_id);

        CREATE TABLE IF NOT EXISTS agent_group_members (
            group_id TEXT NOT NULL REFERENCES agent_groups(id),
            tenant_id TEXT NOT NULL,
            actor_id TEXT NOT NULL,
            added_at TEXT NOT NULL,
            PRIMARY KEY (group_id, actor_id)
        );

        CREATE TABLE IF NOT EXISTS routing_rules (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            intent TEXT,
            label TEXT,
            channel TEXT,
            group_id TEXT NOT NULL,
            priority INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_routing_rules_lookup
            ON routing_rules(tenant_id, priority DESC, id);
        """
    )
