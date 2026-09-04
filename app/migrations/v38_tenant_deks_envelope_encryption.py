"""ROADMAP 43.2: per-tenant data-encryption keys (envelope encryption).

Expand phase: a single additive table holding *wrapped* tenant data-encryption
keys. Plaintext DEK material never touches the database — each row stores the
DEK already encrypted under a KMS-held key-encryption key (``wrapped_dek``),
plus the KEK version used to wrap it so the envelope can unwrap it again.

The key-versioned lifecycle comes from ``app.envelope_crypto``:
``active`` rows decrypt current ciphertext, ``rotated`` rows stay decryptable
(for historical envelopes) until the KEK generation they wrap under is
revoked, and ``revoked`` rows fail closed on every decrypt attempt.
"""

from __future__ import annotations

import sqlite3

from app.migrations import migration


@migration(
    38,
    "per-tenant wrapped data-encryption keys (ROADMAP 43.2)",
    phase="expand",
)
def migrate(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS tenant_deks (
            tenant_id TEXT NOT NULL REFERENCES tenants(id),
            dek_version INTEGER NOT NULL,
            wrapped_dek TEXT NOT NULL,
            kek_version INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            rotated_at TEXT,
            revoked_at TEXT,
            PRIMARY KEY (tenant_id, dek_version)
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_tenant_deks_tenant_status "
        "ON tenant_deks (tenant_id, status)"
    )
