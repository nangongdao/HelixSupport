"""PostgreSQL backend tests.

Two layers:

* Dialect translation tests, which need no database and cover the SQLite ->
  PostgreSQL rewriting rules in :mod:`app.pg_dialect`.
* Integration tests exercising the real backend, which run only when a live
  PostgreSQL instance is configured.

Because :class:`app.postgres_db.PostgresDatabase` inherits its query methods
from the SQLite :class:`app.database.Database`, the two backends cannot drift
apart method-by-method.  The parity test below enforces that invariant.

Run the integration suite with::

    HELIX_PG_INTEGRATION=1 \
    DATABASE_URL="host=localhost port=5432 user=postgres password=... dbname=helix_test" \
    python -m unittest tests.test_postgres
"""

from __future__ import annotations

import inspect
import os
import sqlite3
import unittest
from typing import Any
from unittest.mock import patch

from app.domain import ConversationStatus
from app.pg_compat import (
    COMPAT_FUNCTIONS,
    install_csat_summary_index,
    install_compatibility,
    install_functions,
    install_ordering_columns,
    install_triggers,
    install_updated_sort_index,
)
from app.pg_dialect import PostgresConnection, split_statements, translate


class DialectTranslationTests(unittest.TestCase):
    """SQLite SQL must be rewritten into valid PostgreSQL."""

    def test_placeholders_become_pyformat(self) -> None:
        sql, _ = translate("SELECT * FROM t WHERE a = ? AND b = ?", True)
        self.assertEqual(sql, "SELECT * FROM t WHERE a = %s AND b = %s")

    def test_placeholders_inside_string_literals_are_preserved(self) -> None:
        sql, _ = translate("SELECT 'is it? yes' AS a WHERE b = ?", True)
        self.assertEqual(sql, "SELECT 'is it? yes' AS a WHERE b = %s")

    def test_escaped_quotes_do_not_break_literal_tracking(self) -> None:
        sql, _ = translate("SELECT * FROM t WHERE a = ? AND b = 'don''t'", True)
        self.assertEqual(sql, "SELECT * FROM t WHERE a = %s AND b = 'don''t'")

    def test_literal_percent_is_escaped_only_when_params_present(self) -> None:
        self.assertEqual(translate("SELECT '100%' AS a", False)[0], "SELECT '100%' AS a")
        self.assertEqual(
            translate("SELECT '100%' AS a WHERE b LIKE ?", True)[0],
            "SELECT '100%%' AS a WHERE b LIKE %s",
        )

    def test_insert_or_ignore_becomes_on_conflict(self) -> None:
        sql, _ = translate("INSERT OR IGNORE INTO tenants(id) VALUES (?)", True)
        self.assertEqual(sql, "INSERT INTO tenants(id) VALUES (%s) ON CONFLICT DO NOTHING")

    def test_existing_on_conflict_clause_is_not_duplicated(self) -> None:
        sql, _ = translate(
            "INSERT INTO feedback VALUES (?) ON CONFLICT(id) DO UPDATE SET x = 1", True
        )
        self.assertEqual(sql.upper().count("ON CONFLICT"), 1)

    def test_collate_nocase_is_dropped(self) -> None:
        sql, _ = translate("SELECT * FROM orders WHERE id = ? COLLATE NOCASE", True)
        self.assertNotIn("COLLATE", sql.upper())

    def test_rowid_maps_to_ordering_column(self) -> None:
        """SQLite's rowid tiebreaker maps to the monotonic ``seq`` column."""
        sql, _ = translate("SELECT a FROM m ORDER BY created_at DESC, rowid DESC")
        self.assertEqual(sql, "SELECT a FROM m ORDER BY created_at DESC, seq DESC")

    def test_rowid_substring_is_not_rewritten(self) -> None:
        sql, _ = translate("SELECT growid, my_rowid_col FROM t")
        self.assertEqual(sql, "SELECT growid, my_rowid_col FROM t")

    def test_bare_boolean_placeholder_is_cast(self) -> None:
        """SQLite treats ints as truthy; PostgreSQL requires a real boolean."""
        sql, _ = translate("UPDATE t SET a = CASE WHEN ? THEN NULL ELSE a END", True)
        self.assertIn("CASE WHEN (%s)::int::boolean THEN", sql)

    def test_comparison_in_case_is_left_alone(self) -> None:
        sql, _ = translate("SELECT CASE WHEN ? = 'x' THEN 1 ELSE 0 END", True)
        self.assertNotIn("::boolean", sql)

    def test_begin_immediate_becomes_advisory_lock(self) -> None:
        """The write lock must survive translation, or concurrent claims race."""
        sql, directive = translate("BEGIN IMMEDIATE")
        self.assertIsNone(directive)
        self.assertIn("pg_advisory_xact_lock", sql)

    def test_pragma_is_skipped(self) -> None:
        self.assertEqual(translate("PRAGMA journal_mode = WAL")[1], "skip")

    def test_pragma_table_info_maps_to_information_schema(self) -> None:
        sql, directive = translate("PRAGMA table_info(conversations)")
        self.assertEqual(directive, "table_info:conversations")
        self.assertIn("information_schema.columns", sql)

    def test_sqlite_master_maps_to_catalog_view(self) -> None:
        sql, _ = translate("SELECT name FROM sqlite_master WHERE type='table'")
        self.assertIn("information_schema.tables", sql)
        # The name survives only as the subquery alias, so callers' WHERE
        # clauses on ``name``/``type`` keep working.
        self.assertIn("AS sqlite_master", sql)

    def test_fts_virtual_table_is_reported_unsupported(self) -> None:
        """Signalling 'unsupported' lets the caller's LIKE fallback engage."""
        self.assertEqual(translate("CREATE VIRTUAL TABLE x USING fts5(a)")[1], "unsupported")

    def test_sqlite_triggers_are_skipped(self) -> None:
        """PostgreSQL-native equivalents are installed by app.pg_compat."""
        self.assertEqual(translate("CREATE TRIGGER t BEFORE INSERT ON x")[1], "skip")

    def test_upsert_self_increment_gets_target_table_qualified(self) -> None:
        """Bare ``SET col = col + 1`` is ambiguous on PostgreSQL; the dialect
        qualifies the self-reference with the INSERT target table."""
        sql = (
            "INSERT INTO tenant_usage_daily(tenant_id, date, conversation_count) "
            "VALUES (?, ?, 1) ON CONFLICT(tenant_id, date) DO UPDATE SET "
            "conversation_count = conversation_count + 1"
        )
        translated, _ = translate(sql, has_params=True)
        self.assertIn("conversation_count = tenant_usage_daily.conversation_count + 1", translated)

    def test_upsert_qualified_excluded_references_are_left_alone(self) -> None:
        """``excluded.col`` call-sites are already portable and must not change."""
        sql = (
            "INSERT INTO summaries(tenant_id, cid, kind, content, source, ca, ua) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(tenant_id, conversation_id, kind) DO UPDATE SET "
            "content = excluded.content, source = excluded.source, updated_at = excluded.updated_at"
        )
        translated, _ = translate(sql, has_params=True)
        self.assertIn("content = excluded.content", translated)
        self.assertNotIn("summaries.content", translated)

    def test_upsert_mixed_increment_and_excluded_refs_qualify_only_bare_col(self) -> None:
        sql = (
            "INSERT INTO quality_metrics(tenant_id, date, intent, pv, turn_count, esc) "
            "VALUES (?, ?, ?, ?, 1, ?) "
            "ON CONFLICT(tenant_id, date, intent, prompt_version) DO UPDATE SET "
            "turn_count = turn_count + 1, "
            "escalation_count = escalation_count + excluded.escalation_count"
        )
        translated, _ = translate(sql, has_params=True)
        self.assertIn("turn_count = quality_metrics.turn_count + 1", translated)
        self.assertIn(
            "escalation_count = quality_metrics.escalation_count + excluded.escalation_count",
            translated,
        )

    def test_on_conflict_do_nothing_is_untouched(self) -> None:
        sql = "INSERT INTO t(a, b) VALUES (?, ?) ON CONFLICT(a) DO NOTHING"
        translated, _ = translate(sql, has_params=True)
        self.assertIn("DO NOTHING", translated)
        self.assertNotIn("t.(", translated)


class StatementSplitterTests(unittest.TestCase):
    def test_trigger_body_is_kept_intact(self) -> None:
        script = """
        CREATE TABLE a(id TEXT);
        CREATE TRIGGER t AFTER INSERT ON a
        BEGIN
            UPDATE b SET n = 1;
            UPDATE c SET n = 2;
        END;
        CREATE INDEX i ON a(id);
        """
        statements = split_statements(script)
        self.assertEqual(len(statements), 3)
        self.assertIn("UPDATE c SET n = 2", statements[1])

    def test_case_expression_inside_trigger_does_not_end_the_block(self) -> None:
        """A stray END would be read by PostgreSQL as COMMIT."""
        script = """
        CREATE TRIGGER t AFTER INSERT ON a
        BEGIN
            UPDATE b SET x = CASE WHEN y THEN 1 ELSE 0 END;
        END;
        SELECT 1;
        """
        statements = split_statements(script)
        self.assertEqual(len(statements), 2)
        self.assertIn("CASE WHEN", statements[0])

    def test_semicolon_in_string_literal_is_not_a_separator(self) -> None:
        statements = split_statements("INSERT INTO t VALUES ('a;b'); SELECT 1;")
        self.assertEqual(len(statements), 2)


class BackendParityTests(unittest.TestCase):
    """The two backends must expose one identical API."""

    def test_postgres_inherits_every_sqlite_method(self) -> None:
        from app.database import Database
        from app.postgres_db import PostgresDatabase

        def public_methods(cls: type) -> dict[str, object]:
            return {
                name: member
                for name, member in inspect.getmembers(cls, inspect.isfunction)
                if not name.startswith("_")
            }

        sqlite_methods = public_methods(Database)
        postgres_methods = public_methods(PostgresDatabase)

        missing = sorted(set(sqlite_methods) - set(postgres_methods))
        self.assertEqual(missing, [], f"PostgreSQL backend is missing: {missing}")

        # ``connect`` legitimately differs in its return annotation.
        mismatched = [
            name
            for name in sorted(set(sqlite_methods) & set(postgres_methods))
            if name != "connect"
            and inspect.signature(sqlite_methods[name])  # type: ignore[arg-type]
            != inspect.signature(postgres_methods[name])  # type: ignore[arg-type]
        ]
        self.assertEqual(mismatched, [], f"Signature drift: {mismatched}")

    def test_pool_requires_psycopg2(self) -> None:
        from app.postgres_db import PostgresConnectionPool

        with patch("app.postgres_db._PSYCOPG2_AVAILABLE", False):
            with self.assertRaises(ImportError):
                PostgresConnectionPool("postgresql://localhost/db")


class FakeCursor:
    """Minimal DB-API cursor recording the statements it is given."""

    def __init__(self, connection: "FakeConnection") -> None:
        self._connection = connection
        self.description = None
        self.rowcount = -1
        self._rows: list[Any] = []

    def execute(self, sql: str, params: Any = None) -> None:
        self._connection.statements.append(sql)
        if sql in self._connection.failures:
            raise self._connection.failures[sql]
        if sql.lstrip().upper().startswith("SELECT"):
            self.description = (("value",),)
            self._rows = list(self._connection.rows)
            self.rowcount = len(self._rows)
        else:
            self.description = None
            self._rows = []
            self.rowcount = 1

    def executemany(self, sql: str, seq: Any) -> None:
        self._connection.statements.append(sql)
        if sql in self._connection.failures:
            raise self._connection.failures[sql]
        self.rowcount = len(list(seq))

    def fetchall(self) -> list[Any]:
        return self._rows

    def close(self) -> None:
        self._connection.closed_cursors += 1


class FakeConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.failures: dict[str, Exception] = {}
        self.rows: list[Any] = []
        self.closed_cursors = 0
        self.committed = False
        self.rolled_back = False

    def cursor(self, cursor_factory: Any = None) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        pass


class ConnectionShimTests(unittest.TestCase):
    """The adapter must behave like a sqlite3 connection to its callers."""

    def setUp(self) -> None:
        self.raw = FakeConnection()
        self.conn = PostgresConnection(self.raw)

    def test_statement_is_wrapped_in_a_savepoint(self) -> None:
        """A failed statement must not poison the surrounding transaction."""
        self.conn.execute("SELECT 1 FROM t WHERE a = ?", ("x",))
        self.assertEqual(
            self.raw.statements,
            [
                "SAVEPOINT helix_stmt",
                "SELECT 1 FROM t WHERE a = %s",
                "RELEASE SAVEPOINT helix_stmt",
            ],
        )

    def test_failure_rolls_back_to_savepoint(self) -> None:
        import psycopg2

        self.raw.failures["UPDATE t SET a = 1"] = psycopg2.IntegrityError("duplicate")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE t SET a = 1")
        self.assertIn("ROLLBACK TO SAVEPOINT helix_stmt", self.raw.statements)

    def test_psycopg2_errors_map_to_sqlite_types(self) -> None:
        """app.database catches sqlite3 exceptions by type."""
        import psycopg2

        for source, expected in (
            (psycopg2.IntegrityError("dup"), sqlite3.IntegrityError),
            (psycopg2.ProgrammingError("bad"), sqlite3.OperationalError),
            (psycopg2.OperationalError("down"), sqlite3.OperationalError),
        ):
            with self.subTest(source=type(source).__name__):
                self.raw.failures["SELECT bad"] = source
                with self.assertRaises(expected):
                    self.conn.execute("SELECT bad")

    def test_rows_are_buffered_for_use_after_release(self) -> None:
        self.raw.rows = [{"value": 1}, {"value": 2}]
        result = self.conn.execute("SELECT value FROM t")
        self.assertEqual(result.fetchone(), {"value": 1})
        self.assertEqual(result.fetchall(), [{"value": 2}])
        self.assertEqual(result.fetchall(), [])

    def test_rowcount_is_exposed_for_optimistic_updates(self) -> None:
        result = self.conn.execute("UPDATE t SET a = 1")
        self.assertEqual(result.rowcount, 1)

    def test_skipped_statements_never_reach_the_server(self) -> None:
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.assertEqual(self.raw.statements, [])

    def test_unsupported_statement_raises_for_caller_fallback(self) -> None:
        with self.assertRaises(sqlite3.OperationalError):
            self.conn.execute("CREATE VIRTUAL TABLE x USING fts5(a)")

    def test_executescript_runs_each_statement(self) -> None:
        self.conn.executescript("CREATE TABLE a(id TEXT); CREATE INDEX i ON a(id);")
        executed = [s for s in self.raw.statements if not s.endswith("helix_stmt")]
        self.assertEqual(len(executed), 2)

    def test_executemany_with_no_rows_is_a_noop(self) -> None:
        result = self.conn.executemany("INSERT INTO t VALUES (?)", [])
        self.assertEqual(result.rowcount, 0)
        self.assertEqual(self.raw.statements, [])

    def test_executemany_translates_and_counts(self) -> None:
        result = self.conn.executemany("INSERT INTO t VALUES (?)", [("a",), ("b",)])
        self.assertEqual(result.rowcount, 2)
        self.assertIn("INSERT INTO t VALUES (%s)", self.raw.statements)

    def test_cursors_are_always_closed(self) -> None:
        self.conn.execute("SELECT 1")
        with self.assertRaises(sqlite3.DatabaseError):
            import psycopg2

            self.raw.failures["SELECT boom"] = psycopg2.Error("boom")
            self.conn.execute("SELECT boom")
        self.assertEqual(self.raw.closed_cursors, 2)

    def test_create_function_is_a_noop(self) -> None:
        """SQLite UDFs are replaced by server-side functions."""
        self.conn.create_function("helix_search_terms", 1, str)


class CompatibilityInstallTests(unittest.TestCase):
    """The compat layer must install cleanly and fail atomically."""

    def test_functions_install_before_triggers(self) -> None:
        """Schema backfill queries call these, so ordering matters."""
        raw = FakeConnection()
        install_functions(raw)
        self.assertTrue(raw.committed)
        self.assertEqual(len(raw.statements), len(COMPAT_FUNCTIONS))

    def test_triggers_are_dropped_before_being_created(self) -> None:
        raw = FakeConnection()
        install_triggers(raw)
        drops = [s for s in raw.statements if s.startswith("DROP TRIGGER")]
        creates = [s for s in raw.statements if s.startswith("CREATE TRIGGER")]
        self.assertTrue(drops)
        self.assertEqual(len(drops), len(creates))
        self.assertLess(raw.statements.index(drops[0]), raw.statements.index(creates[0]))

    def test_knowledge_ordering_column_is_sequence_backed_and_backfilled(self) -> None:
        raw = FakeConnection()
        install_ordering_columns(raw)
        ddl = "\n".join(raw.statements)
        self.assertIn("CREATE SEQUENCE IF NOT EXISTS knowledge_articles_seq", ddl)
        self.assertIn("ALTER TABLE knowledge_articles ADD COLUMN", ddl)
        self.assertIn("DEFAULT nextval('knowledge_articles_seq')", ddl)
        self.assertIn("UPDATE knowledge_articles", ddl)

    def test_message_summary_trigger_is_bound(self) -> None:
        """Without it, message_count and preview silently stop updating."""
        raw = FakeConnection()
        install_triggers(raw)
        self.assertTrue(
            any(
                "messages_summary_insert" in s and "AFTER INSERT ON messages" in s
                for s in raw.statements
            )
        )

    def test_channel_tenant_guard_triggers_are_bound(self) -> None:
        raw = FakeConnection()
        install_triggers(raw)
        ddl = "\n".join(raw.statements)
        self.assertIn("channel_threads_tenant_guard", ddl)
        self.assertIn("BEFORE INSERT ON channel_threads", ddl)
        self.assertIn("channel_receipts_tenant_guard", ddl)
        self.assertIn("BEFORE INSERT ON channel_webhook_receipts", ddl)

    def test_csat_summary_index_is_partial_and_idempotent(self) -> None:
        raw = FakeConnection()
        install_csat_summary_index(raw)
        self.assertTrue(raw.committed)
        self.assertEqual(len(raw.statements), 1)
        statement = raw.statements[0]
        self.assertIn("idx_csat_summary_tenant_responded", statement)
        self.assertIn("substr(responded_at, 1, 10)", statement)
        self.assertIn("WHERE rating IS NOT NULL", statement)

    def test_updated_search_index_has_complete_sort_order(self) -> None:
        raw = FakeConnection()
        install_updated_sort_index(raw)
        self.assertTrue(raw.committed)
        self.assertEqual(len(raw.statements), 1)
        statement = raw.statements[0]
        self.assertIn("idx_conversations_tenant_updated_id", statement)
        self.assertIn("tenant_id, updated_at DESC, id DESC", statement)

    def test_failure_rolls_back(self) -> None:
        raw = FakeConnection()
        raw.failures[COMPAT_FUNCTIONS[1]] = RuntimeError("boom")
        with self.assertRaises(RuntimeError):
            install_functions(raw)
        self.assertTrue(raw.rolled_back)
        self.assertFalse(raw.committed)

    def test_install_compatibility_does_both(self) -> None:
        raw = FakeConnection()
        install_compatibility(raw)
        self.assertTrue(any(s.startswith("CREATE TRIGGER") for s in raw.statements))
        self.assertTrue(any("json_extract" in s for s in raw.statements))


@unittest.skipUnless(
    os.getenv("HELIX_PG_INTEGRATION") and os.getenv("DATABASE_URL"),
    "Requires PostgreSQL instance and HELIX_PG_INTEGRATION=1",
)
class PostgresIntegrationTests(unittest.TestCase):
    """Exercise the real backend against a live PostgreSQL instance."""

    TENANT = "pg-test-tenant"

    @classmethod
    def setUpClass(cls) -> None:
        import psycopg2

        from app.postgres_db import create_postgres_database

        # Start from a clean schema so repeat runs are deterministic.
        connection = psycopg2.connect(os.environ["DATABASE_URL"])
        connection.autocommit = True
        connection.cursor().execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        connection.close()

        cls.db = create_postgres_database(os.environ["DATABASE_URL"])
        cls.db.ensure_tenant(cls.TENANT)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.db.close()

    def _conversation(self, name: str = "PG Customer") -> dict:
        return self.db.create_conversation(self.TENANT, name, "CUST-PG-1", "api", "admin", 120)

    def test_create_and_get_conversation(self) -> None:
        conversation = self._conversation()
        self.assertTrue(conversation["id"].startswith("conv_"))
        fetched = self.db.get_conversation(self.TENANT, conversation["id"])
        assert fetched is not None
        self.assertEqual(fetched["customer_name"], "PG Customer")
        self.assertEqual(fetched["labels"], [])

    def test_formal_channel_mapping_receipt_and_job_id_are_durable(self) -> None:
        conversation, created = self.db.get_or_create_channel_conversation(
            self.TENANT,
            "pg-formal-account",
            "pg-external-thread",
            "PG Channel Customer",
            "CUST-PG-CHANNEL",
            "formal_chat",
            "channel:pg-formal-account",
            120,
        )
        replayed_conversation, replay_created = self.db.get_or_create_channel_conversation(
            self.TENANT,
            "pg-formal-account",
            "pg-external-thread",
            "PG Channel Customer",
            "CUST-PG-CHANNEL",
            "formal_chat",
            "channel:pg-formal-account",
            120,
        )
        self.assertTrue(created)
        self.assertFalse(replay_created)
        self.assertEqual(replayed_conversation["id"], conversation["id"])

        receipt, receipt_created = self.db.claim_channel_webhook_receipt(
            self.TENANT,
            "pg-formal-account",
            "pg-message-1",
            "pg-external-thread",
            conversation["id"],
            "a" * 64,
        )
        self.assertTrue(receipt_created)
        self.assertEqual(receipt["conversation_id"], conversation["id"])

        job, job_replayed = self.db.enqueue_turn_job(
            self.TENANT,
            conversation["id"],
            "phase38-pg-idempotency",
            "channel:pg-formal-account",
            "PostgreSQL channel message",
            3,
            "pg-message-1",
        )
        self.assertFalse(job_replayed)
        self.assertEqual(job["channel_message_id"], "pg-message-1")

    def test_timestamps_are_iso_strings_not_datetimes(self) -> None:
        """The API layer serialises these directly, so the type matters."""
        conversation = self._conversation()
        self.assertIsInstance(conversation["created_at"], str)

    def test_message_triggers_maintain_conversation_summary(self) -> None:
        """message_count/preview/needs_response are trigger-maintained."""
        conversation = self._conversation("Trigger Test")
        self.db.add_message(self.TENANT, conversation["id"], "customer", "C", "hello pg")

        updated = self.db.get_conversation(self.TENANT, conversation["id"])
        assert updated is not None
        self.assertEqual(updated["message_count"], 1)
        self.assertEqual(updated["preview"], "hello pg")
        self.assertEqual(updated["needs_response"], 1)

        self.db.add_message(self.TENANT, conversation["id"], "assistant", "A", "on it")
        answered = self.db.get_conversation(self.TENANT, conversation["id"])
        assert answered is not None
        self.assertEqual(answered["message_count"], 2)
        self.assertEqual(answered["needs_response"], 0)
        self.assertIsNotNone(answered["first_response_at"])

    def test_tenant_guard_trigger_rejects_cross_tenant_message(self) -> None:
        conversation = self._conversation("Guard Test")
        self.db.ensure_tenant("other-tenant")
        with self.assertRaises(Exception):
            self.db.add_message("other-tenant", conversation["id"], "customer", "C", "nope")

    def test_list_messages_order_is_deterministic(self) -> None:
        """Same-microsecond inserts order by the monotonic ``seq`` column.

        The UUID message id is random, so an ``id`` tiebreak would scramble a
        transcript when two messages land in the same instant.  The ``seq``
        compatibility column (filled from a sequence) makes ordering match
        insertion order exactly, as the SQLite backend's native ``rowid`` does.
        """
        conversation = self._conversation("Ordering")
        for index in range(3):
            self.db.add_message(
                self.TENANT, conversation["id"], "customer", "C", f"message {index}"
            )
        messages = self.db.list_messages(self.TENANT, conversation["id"])
        self.assertEqual(len(messages), 3)
        seqs = [m["seq"] for m in messages]
        self.assertEqual(seqs, sorted(seqs), "seq must be monotonic")
        self.assertEqual(len(set(seqs)), len(seqs), "seq values must be distinct")
        self.assertEqual([m["content"] for m in messages], ["message 0", "message 1", "message 2"])

    def test_tenant_exists(self) -> None:
        self.assertTrue(self.db.tenant_exists(self.TENANT))
        self.assertFalse(self.db.tenant_exists("no-such-tenant"))

    def test_ping(self) -> None:
        self.assertTrue(self.db.ping())

    def test_set_routing_applies_full_outcome(self) -> None:
        conversation = self._conversation("Routing")
        updated = self.db.set_routing(
            self.TENANT,
            conversation["id"],
            ConversationStatus.WAITING_HUMAN,
            "refund_request",
            "escalation",
            "high",
            "policy_flag",
            0.91,
            30,
        )
        self.assertTrue(updated)

        routed = self.db.get_conversation(self.TENANT, conversation["id"])
        assert routed is not None
        self.assertEqual(routed["status"], "waiting_human")
        self.assertEqual(routed["intent"], "refund_request")
        self.assertEqual(routed["assigned_agent"], "escalation")
        self.assertEqual(routed["priority"], "high")
        self.assertEqual(routed["handoff_reason"], "policy_flag")

    def test_set_routing_ignores_closed_conversation(self) -> None:
        """The status guard prevents routing a conversation that moved on."""
        conversation = self._conversation("Closed Routing")
        self.db.transition_conversation(
            self.TENANT, conversation["id"], [ConversationStatus.OPEN], ConversationStatus.RESOLVED
        )
        updated = self.db.set_routing(
            self.TENANT,
            conversation["id"],
            ConversationStatus.WAITING_HUMAN,
            "x",
            "a",
            "normal",
            None,
            0.5,
            30,
        )
        self.assertFalse(updated)

    def test_turn_idempotency_replays_completed_response(self) -> None:
        conversation = self._conversation("Idempotency")
        status, replay = self.db.claim_turn(self.TENANT, conversation["id"], "key-1", 60)
        self.assertEqual(status, "new")
        self.assertIsNone(replay)

        self.db.complete_turn(self.TENANT, conversation["id"], "key-1", {"reply": "done"})

        status, replay = self.db.claim_turn(self.TENANT, conversation["id"], "key-1", 60)
        self.assertEqual(status, "completed")
        self.assertEqual(replay, {"reply": "done"})

    def test_enqueue_claim_and_complete_job(self) -> None:
        conversation = self._conversation("Queue")
        job, replayed = self.db.enqueue_turn_job(
            self.TENANT, conversation["id"], "pg-idem-key", "actor", "content", 3
        )
        self.assertFalse(replayed)
        self.assertEqual(job["status"], "queued")

        claimed = self.db.claim_next_turn_job("pg-worker", 300)
        assert claimed is not None
        self.assertEqual(claimed["status"], "processing")
        self.assertEqual(claimed["locked_by"], "pg-worker")

        self.assertTrue(self.db.complete_turn_job(job["id"], "pg-worker", {"result": "ok"}, 300))

    def test_claim_turn_job_by_id(self) -> None:
        """The Redis queue reconciles a specific job against PostgreSQL."""
        conversation = self._conversation("Claim By Id")
        job, _ = self.db.enqueue_turn_job(
            self.TENANT, conversation["id"], "by-id-key", "actor", "content", 3
        )
        claimed = self.db.claim_turn_job_by_id(job["id"], "redis-worker", 300)
        assert claimed is not None
        self.assertEqual(claimed["status"], "processing")
        self.assertEqual(claimed["locked_by"], "redis-worker")
        self.assertEqual(claimed["id"], job["id"])

        # A second claim of the same id is refused (already in flight).
        self.assertIsNone(self.db.claim_turn_job_by_id(job["id"], "other-worker", 300))
        self.assertTrue(self.db.complete_turn_job(job["id"], "redis-worker", {"ok": True}, 300))

    def test_enqueue_is_idempotent(self) -> None:
        conversation = self._conversation("Queue Idempotency")
        first, replayed_first = self.db.enqueue_turn_job(
            self.TENANT, conversation["id"], "dup-key", "actor", "content", 3
        )
        second, replayed_second = self.db.enqueue_turn_job(
            self.TENANT, conversation["id"], "dup-key", "actor", "content", 3
        )
        self.assertFalse(replayed_first)
        self.assertTrue(replayed_second)
        self.assertEqual(first["id"], second["id"])

    def test_no_duplicate_claim_across_workers(self) -> None:
        """The advisory lock must preserve SQLite's BEGIN IMMEDIATE guarantee."""
        conversation = self._conversation("Claim Race")
        self.db.enqueue_turn_job(self.TENANT, conversation["id"], "race-key", "actor", "content", 3)
        first = self.db.claim_next_turn_job("worker-a", 300)
        second = self.db.claim_next_turn_job("worker-b", 300)
        assert first is not None
        self.assertNotEqual(first["id"], (second or {}).get("id"))

    def test_search_knowledge_uses_tag_fallback(self) -> None:
        """FTS5 is SQLite-only, so this backend always takes the LIKE/tag path.

        That path matches on tags and exact titles rather than body text —
        the same behaviour the SQLite backend exhibits when FTS is
        unavailable, so the two remain consistent.
        """
        self.db.create_knowledge(
            self.TENANT,
            "Refund window",
            "Refunds accepted within 30 days",
            ["refund", "policy"],
            "policy",
            "/kb",
        )
        results = self.db.search_knowledge(self.TENANT, "refund", 3)
        self.assertTrue(any("Refund" in item["title"] for item in results))

    def test_audit_and_dashboard(self) -> None:
        conversation = self._conversation("Audit")
        self.db.audit(self.TENANT, conversation["id"], "admin", "pg.test_event", {"key": "value"})
        events = self.db.list_audit(self.TENANT, conversation["id"])
        self.assertTrue(any(event["event_type"] == "pg.test_event" for event in events))

        dashboard = self.db.dashboard(self.TENANT)
        self.assertGreater(dashboard["total"], 0)

    def test_audit_chain_continues_after_retention_archive(self) -> None:
        from app.retention import RetentionService

        self.db.audit(self.TENANT, None, "admin", "pg.archive.old", {})
        service = RetentionService(self.db)
        self.assertGreater(
            service.enforce_retention(self.TENANT, "audit_events", retention_days=-1),
            0,
        )
        archived_head = self.db.audit_chain_head()
        self.db.audit(self.TENANT, None, "admin", "pg.archive.new", {})
        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT prev_hash, event_hash FROM audit_events "
                "WHERE tenant_id = ? AND event_type = ?",
                (self.TENANT, "pg.archive.new"),
            ).fetchone()
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["prev_hash"], archived_head)
        self.assertEqual(self.db.audit_chain_head(), row["event_hash"])

    def test_performance_stats_reports_backend(self) -> None:
        stats = self.db.performance_stats()
        self.assertEqual(stats["backend"], "postgresql")

    # -- ROADMAP 18.2: trigram message search alignment --------------------

    def test_trgm_search_extension_and_index_installed(self) -> None:
        """``initialize()`` created the pg_trgm extension and a GIN index.

        This is the §18.5/CAPACITY-3.2 acceptance prerequisite: without it,
        message search on PostgreSQL is a full-table LIKE scan.
        """
        import psycopg2

        connection = psycopg2.connect(os.environ["DATABASE_URL"])
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'")
                self.assertTrue(cursor.fetchone(), "pg_trgm extension is missing")
                cursor.execute(
                    "SELECT indexdef FROM pg_indexes WHERE indexname = 'idx_messages_content_trgm'"
                )
                row = cursor.fetchone()
                self.assertIsNotNone(row, "messages trigram index is missing")
                assert row is not None
                self.assertIn("gin_trgm_ops", row[0])
        finally:
            connection.close()

    def test_updated_search_index_serves_candidate_window(self) -> None:
        """The dense-search candidate window can read in final sort order."""
        import psycopg2

        connection = psycopg2.connect(os.environ["DATABASE_URL"])
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET enable_seqscan = off")
                cursor.execute(
                    "EXPLAIN SELECT id FROM conversations WHERE tenant_id = %s "
                    "ORDER BY updated_at DESC, id DESC LIMIT 50",
                    (self.TENANT,),
                )
                plan = "\n".join(row[0] for row in cursor.fetchall())
                cursor.execute("RESET enable_seqscan")
            self.assertIn("idx_conversations_tenant_updated_id", plan)
        finally:
            connection.close()

    def test_updated_sort_index_installed(self) -> None:
        """``initialize()`` created the ``updated DESC, id DESC`` sort index.

        This is the §18.5 search fast-path prerequisite: the shared queue
        ORDER BY ends in ``id DESC``, so without the trailing column the
        planner abandons the ordered index for a full parallel scan + top-N
        sort on the 100k-tenant (measured ~255ms warm; the index takes the
        ordered scan down to <1ms).
        """
        import psycopg2

        connection = psycopg2.connect(os.environ["DATABASE_URL"])
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE indexname = 'idx_conversations_tenant_updated_id'"
                )
                row = cursor.fetchone()
                self.assertIsNotNone(row, "updated-sort index is missing")
                assert row is not None
                self.assertIn("updated_at DESC, id DESC", row[0])
        finally:
            connection.close()

    def test_csat_summary_index_installed(self) -> None:
        """The native post-schema pass mirrors SQLite migration 25."""
        import psycopg2

        connection = psycopg2.connect(os.environ["DATABASE_URL"])
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE indexname = 'idx_csat_summary_tenant_responded'"
                )
                row = cursor.fetchone()
                self.assertIsNotNone(row, "CSAT summary index is missing")
                assert row is not None
                self.assertIn("WHERE (rating IS NOT NULL)", row[0])
        finally:
            connection.close()

    def test_message_search_finds_match_through_trgm_path(self) -> None:
        """A distinctive >=3-char term (index-accelerable) finds the row."""
        conversation = self._conversation("Trgm Search")
        self.db.add_message(
            self.TENANT, conversation["id"], "customer", "C", "please check the zebranaut order"
        )
        self.db.add_message(self.TENANT, conversation["id"], "assistant", "A", "done reviewing")
        rows = self.db.list_conversations(self.TENANT, search="zebranaut")
        self.assertIn(conversation["id"], [row["id"] for row in rows], "search missed the row")
        self.assertFalse(
            self.db._message_fts_enabled,
            "PG must stay on the LIKE fallback (no FTS mirror)",
        )

    def test_updated_sort_search_fast_path(self) -> None:
        """The ``sort="updated"`` windowed fast path engages on live PG.

        Twelve matching conversations fill the ``limit=10`` window, so the fast
        path serves a complete page from the newest conversations; an offset
        past the window cap bypasses the window entirely and falls through to
        the aggregated CTE, which on this tiny tenant is empty.  A spy on
        ``_query_conversations_windowed`` proves the shallow page was actually
        served by the window (returned non-None) instead of letting the CTE
        fallback satisfy the page assertions.
        """
        for index in range(12):
            conversation = self._conversation(f"Window Search {index}")
            self.db.add_message(
                self.TENANT,
                conversation["id"],
                "customer",
                "C",
                "please check the windowpane order",
            )
        with patch.object(
            self.db,
            "_query_conversations_windowed",
            wraps=self.db._query_conversations_windowed,
        ) as windowed:
            fast = self.db.list_conversations(
                self.TENANT, search="windowpane", sort="updated", limit=10, offset=0
            )
            # offset 1024 + limit 10 exceeds the window cap, so the windowed
            # method must not even be called: the gate sends it to the CTE.
            deep = self.db.list_conversations(
                self.TENANT, search="windowpane", sort="updated", limit=10, offset=1024
            )
        # Ground truth: the newest ten conversations in the same recency order.
        expected = [
            row["id"] for row in self.db.list_conversations(self.TENANT, sort="updated", limit=10)
        ]
        self.assertEqual(10, len(fast), "fast path should fill the page")
        self.assertEqual(
            [row["id"] for row in fast],
            expected,
            "fast path should return the newest ten seeds in recency order",
        )
        self.assertEqual(
            windowed.call_count, 1, "only the shallow page should reach the windowed fast path"
        )
        self.assertIsNotNone(
            windowed.return_value, "windowed fast path must fill and return the page"
        )
        self.assertEqual([], [row["id"] for row in deep], "fallback returned unexpected rows")

    def test_trgm_index_serves_the_like_predicate(self) -> None:
        """The fallback's LIKE predicate is served by an index, not a seq scan.

        With a few dozen rows the planner may prefer a cheaper btree bitmap over
        the trgm GIN, so this unit test asserts the *shape* (no seq scan) plus
        the opclass capability asserted above; the production plan choice and
        its timing are asserted separately by the CAPACITY benchmark.
        """
        import psycopg2

        connection = psycopg2.connect(os.environ["DATABASE_URL"])
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET enable_seqscan = off")
                cursor.execute(
                    "EXPLAIN SELECT 1 FROM messages m "
                    "WHERE m.tenant_id = %s AND m.content LIKE '%%zebranaut%%'",
                    (self.TENANT,),
                )
                plan = "\n".join(row[0] for row in cursor.fetchall())
                cursor.execute("RESET enable_seqscan")
            self.assertNotIn(
                "Seq Scan",
                plan,
                f"LIKE predicate degraded to a full scan:\n{plan}",
            )
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
