"""N/N+1 rolling-migration data-integrity matrix (Gate C).

``check_expand_additivity`` proves every expand migration's *schema* delta
is a superset; this suite proves the *rows* survive the chain unchanged.
A database seeded at the v01 baseline (the first migration any real
deployment starts from) with representative tenant/accounting rows must
round-trip through the full chain to v44 with every seed value bit-identical
— no migration may rewrite, touch, or default-munge existing rows while
rolling forward.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.migrations import all_migrations, run_migrations, migration_schema_version

SEED_TENANT = "demo"


def _seed_baseline(connection: sqlite3.Connection) -> None:
    """Insert representative rows into the v01 baseline tables."""
    connection.execute(
        "INSERT INTO tenants (id, name, created_at) VALUES (?, 'Demo', ?)",
        (SEED_TENANT, "2026-01-01T00:00:00+00:00"),
    )
    connection.execute(
        """INSERT INTO conversations
        (id, tenant_id, customer_name, customer_ref, channel, status, intent,
         assigned_agent, priority, handoff_reason, sla_due_at, last_confidence,
         version, preview, message_count, last_message_at, labels_json,
         claimed_by, claimed_at, claim_expires_at, needs_response,
         waiting_since, first_response_at, created_at, updated_at, resolved_at)
        VALUES ('c1', 'demo', 'Alice', 'REF-1', 'web', 'open', 'order',
                'agent-1', 'high', NULL, '2026-01-02T00:00:00+00:00', 0.97,
                3, 'preview text', 2, '2026-01-01T01:00:00+00:00', '["a","b"]',
                'agent-1', '2026-01-01T00:30:00+00:00', '2026-01-01T02:00:00+00:00',
                1, '2026-01-01T00:05:00+00:00', '2026-01-01T00:02:00+00:00',
                '2026-01-01T00:00:00+00:00', '2026-01-01T01:00:00+00:00', NULL)""",
    )
    connection.execute(
        """INSERT INTO messages
        (id, tenant_id, conversation_id, turn_id, role, author, content,
         metadata_json, created_at, seq)
        VALUES ('m1', 'demo', 'c1', 't1', 'customer', 'Alice',
                'hello', '{"lang":"zh"}', '2026-01-01T00:00:00+00:00', 1)""",
    )
    connection.execute(
        """INSERT INTO audit_events
        (id, tenant_id, conversation_id, request_id, actor, event_type,
         payload_json, created_at, seq)
        VALUES ('a1', 'demo', 'c1', 'req-1', 'system', 'conversation.created',
                '{"where":"seed"}', '2026-01-01T00:00:00+00:00', 1)""",
    )
    connection.execute(
        """INSERT INTO turn_requests
        (tenant_id, conversation_id, idempotency_key, status, response_json,
         error_code, created_at, updated_at)
        VALUES ('demo', 'c1', 'idem-1', 'completed', '{"ok":true}', NULL,
                '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')""",
    )
    connection.execute(
        """INSERT INTO knowledge_articles
        (id, tenant_id, title, content, tags, category, source_url,
         active, version, updated_at)
        VALUES ('k1', 'demo', 'Shipping', 'Policy', '["kb"]', 'general',
                'https://example.test/1', 1, 1, '2026-01-01T00:00:00+00:00')""",
    )


def _select_all_rows(connection: sqlite3.Connection) -> dict[str, list[tuple]]:
    """Snapshot every table's rows as (column-name, value) tuples, keyed by rowid."""
    snapshot: dict[str, list[tuple]] = {}
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        if not row[0].startswith("sqlite_")
    }
    for table in tables:
        cursor = connection.execute(f"SELECT * FROM {table} ORDER BY rowid")
        rows = cursor.fetchall()
        columns = [description[0] for description in cursor.description]
        snapshot[table] = [tuple(zip(columns, row)) for row in rows]
    return snapshot


def _intersection_match(before: dict[str, list[tuple]], after: dict[str, list[tuple]]) -> list[str]:
    """Rows must keep every pre-existing column value bit-identical; columns
    added by later migrations (with defaults or backfills) are allowed to
    appear at the row tail — that is the expand contract, not a data change.
    """
    problems: list[str] = []
    for table, rows in before.items():
        if table == "schema_migrations":
            # Framework bookkeeping: rows accumulate as the chain applies.
            continue
        if table not in after:
            problems.append(f"table {table} disappeared")
            continue
        after_rows = after[table]
        if len(rows) != len(after_rows):
            problems.append(f"table {table}: row count changed {len(rows)} -> {len(after_rows)}")
            continue
        for index, (before_row, after_row) in enumerate(zip(rows, after_rows)):
            before_cols = dict(before_row)
            after_cols = dict(after_row)
            for column, value in before_cols.items():
                if column not in after_cols:
                    problems.append(f"table {table} row {index}: column {column} disappeared")
                elif after_cols[column] != value:
                    problems.append(
                        f"table {table} row {index}: {column} changed "
                        f"{value!r} -> {after_cols[column]!r}"
                    )
    return problems


class DataPreservationMatrixTests(unittest.TestCase):
    def test_seed_rows_survive_every_migration_step(self) -> None:
        migrations = all_migrations()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "matrix.db"
            connection = sqlite3.connect(path)
            try:
                connection.row_factory = sqlite3.Row
                run_migrations(connection, migrations[:1])
                _seed_baseline(connection)
                connection.commit()
                before = _select_all_rows(connection)

                # Step through the chain one migration at a time; after every
                # step the pre-existing rows must still carry every column
                # value bit-identical. This catches a migration that rewrites
                # or munges old data at the exact step that does it.
                for step in range(1, len(migrations)):
                    run_migrations(connection, migrations[step : step + 1])
                    connection.commit()
                    after = _select_all_rows(connection)
                    problems = _intersection_match(before, after)
                    self.assertEqual(
                        problems,
                        [],
                        f"seed rows changed at migration step {migrations[step].version}",
                    )
                self.assertEqual(
                    migration_schema_version(connection),
                    migrations[-1].version,
                    "chain must reach the head",
                )
            finally:
                connection.close()

    def test_repeat_apply_is_idempotent_on_data(self) -> None:
        migrations = all_migrations()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "idem.db"
            connection = sqlite3.connect(path)
            try:
                connection.row_factory = sqlite3.Row
                run_migrations(connection, migrations)
                _seed_baseline(connection)
                connection.commit()
                before = _select_all_rows(connection)

                # Second pass: everything is already applied, so nothing may
                # change any row (columns added by the chain keep their values).
                run_migrations(connection, migrations)
                connection.commit()
                after = _select_all_rows(connection)
                self.assertEqual(after, before)
            finally:
                connection.close()

    def test_red_light_data_rewrite_is_caught(self) -> None:
        """A migration that rewrites an existing row must fail the matrix."""
        from app.migrations import Migration

        def bad_migration(connection: sqlite3.Connection) -> None:
            connection.execute("UPDATE conversations SET customer_name = 'HACKED' WHERE id = 'c1'")

        migrations = all_migrations()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "red.db"
            connection = sqlite3.connect(path)
            try:
                connection.row_factory = sqlite3.Row
                run_migrations(connection, migrations[:1])
                _seed_baseline(connection)
                connection.commit()
                before = _select_all_rows(connection)

                bad = Migration(99, "bad rewrite", up=bad_migration, phase="expand")
                run_migrations(connection, [bad])
                connection.commit()
                after = _select_all_rows(connection)
                problems = _intersection_match(before, after)
                self.assertEqual(len(problems), 1)
                self.assertIn("customer_name changed", problems[0])
            finally:
                connection.close()


@unittest.skipUnless(
    os.getenv("HELIX_PG_INTEGRATION") and os.getenv("DATABASE_URL"),
    "Requires PostgreSQL instance and HELIX_PG_INTEGRATION=1",
)
class PostgresDataPreservationTests(unittest.TestCase):
    """The same seed-then-migrate matrix against the live PostgreSQL backend.

    The migration chain runs through ``pg_dialect``'s translated SQL, so a
    migration that is additive on SQLite but drops/rewrites rows via a
    PostgreSQL-only spelling would escape the in-memory SQLite check. Seeding
    at v01 and stepping to v44 on the real backend closes that gap.
    """

    def test_seed_rows_survive_full_chain_on_postgres(self) -> None:
        import psycopg2

        from app.pg_dialect import PostgresConnection

        migrations = all_migrations()
        url = os.environ["DATABASE_URL"]

        # Start from a clean schema so repeat runs are deterministic.
        connection = psycopg2.connect(url)
        connection.autocommit = True
        connection.cursor().execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        connection.close()

        raw = psycopg2.connect(url)
        try:
            pc = PostgresConnection(raw)
            run_migrations(pc, migrations[:1])  # type: ignore[arg-type]
            raw.commit()
            _seed_baseline(pc)  # type: ignore[arg-type]
            raw.commit()
            # Snapshot the seeded tables through the translation layer.
            before: dict[str, list[tuple]] = {}
            for table in (
                "tenants",
                "conversations",
                "messages",
                "audit_events",
                "knowledge_articles",
                "turn_requests",
            ):
                try:
                    result = pc.execute(f"SELECT * FROM {table}")
                    before[table] = [tuple(row.items()) for row in result.fetchall()]
                except Exception:
                    before[table] = []

            run_migrations(pc, migrations[1:])  # type: ignore[arg-type]
            raw.commit()
            self.assertEqual(
                migration_schema_version(pc),
                migrations[-1].version,  # type: ignore[arg-type], "chain must reach head"
            )
            after = {}
            for table, rows in before.items():
                if not rows:
                    continue
                result = pc.execute(f"SELECT * FROM {table}")
                after[table] = [tuple(row.items()) for row in result.fetchall()]
            problems = _intersection_match(before, after)
            self.assertEqual(problems, [], "seed rows changed across the PG chain")
            # The demo tenant seeded via Database.initialize is absent on the
            # raw connection path; assert the seed itself landed.
            self.assertGreaterEqual(len(before.get("tenants", [])), 1)
        finally:
            raw.close()


if __name__ == "__main__":
    unittest.main()
