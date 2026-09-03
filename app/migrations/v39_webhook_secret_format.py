"""ROADMAP 43.2: webhook endpoint secrets may be envelope-encrypted.

Expand phase: the ``secret`` column previously held the plaintext HMAC
signing secret for every endpoint. A ``secret_format`` discriminator lets new
endpoints store an envelope-encrypted value (``envelope``, key versions
travelling with the ciphertext) while existing rows keep the legacy
``plain`` default. Nothing is re-encrypted here; the runtime writes the
discriminator at registration and fails closed (dead-letters) any delivery
whose secret cannot be decrypted.
"""

from __future__ import annotations

import sqlite3

from app.migrations import migration


@migration(
    39,
    "webhook endpoint secret format discriminator (ROADMAP 43.2)",
    phase="expand",
)
def migrate(connection: sqlite3.Connection) -> None:
    connection.execute(
        "ALTER TABLE webhook_endpoints ADD COLUMN secret_format TEXT NOT NULL DEFAULT 'plain'"
    )
