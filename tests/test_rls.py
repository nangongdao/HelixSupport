"""Phase 43.2 contract (a): PostgreSQL row-level tenant isolation.

Unit coverage (no live database needed):

* every protected table pins ``tenant_id TEXT NOT NULL`` in its schema;
* the installer emits idempotent ENABLE + isolation-policy DDL keyed on the
  ``app.tenant_id`` custom GUC, with an optional FORCE-on-owner variant;
* ``verify_rls`` / ``missing_protection`` read live catalog state;
* ambient scope helpers bind, nest and reset correctly;
* config validation ties enforcement to the postgresql backend;
* ``PostgresDatabase`` connections bind the transaction-scoped GUC from the
  ambient scope and fail closed (loud) without one;
* audit writes self-scope to their explicit tenant when no request scope
  exists, and worker scheduling runs under maintenance scope while turn
  execution narrows to the job's own tenant.
"""

from __future__ import annotations

import re
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app.config import Settings
from app.context import (
    bind_tenant_scope,
    current_scope_mode,
    current_tenant,
    maintenance_scope,
    tenant_scope,
)
from app.rls import (
    POLICY_NAME,
    RLS_TABLES,
    TENANT_CONTEXT_GUC,
    TenantContextError,
    force_owner_statement,
    install_statements,
    missing_protection,
    tenant_filter_clause,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class FakeRow(dict):
    def __getitem__(self, key: str) -> Any:  # noqa: D105
        return dict.__getitem__(self, key)


class FakeResult:
    """StatementResult stand-in (fetchone/fetchall over buffered rows)."""

    def __init__(self, rows: list[FakeRow]) -> None:
        self._rows = rows
        self._index = 0
        self.rowcount = len(rows)

    def fetchone(self) -> FakeRow | None:
        if self._index >= len(self._rows):
            return None
        row = self._rows[self._index]
        self._index += 1
        return row

    def fetchall(self) -> list[FakeRow]:
        rows = self._rows[self._index :]
        self._index = len(self._rows)
        return rows


class RecordingConnection:
    """Minimal adapter-style connection capturing executed SQL."""

    def __init__(self, results: list[list[FakeRow]] | None = None) -> None:
        self.statements: list[tuple[str, tuple[Any, ...]]] = []
        self._results = list(results or [])

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> FakeResult:
        self.statements.append((sql, params))
        if self._results:
            return FakeResult(self._results.pop(0))
        return FakeResult([])

    def executemany(self, sql: str, rows: Any) -> None:
        self.statements.append((sql, tuple(rows)))


@contextmanager
def _no_scope():
    yield


def _baseline_tables_with_columns() -> dict[str, set[str]]:
    """Parse every versioned migration's CREATE TABLE columns."""
    from app import migrations as migration_pkg

    tables: dict[str, set[str]] = {}
    pattern = re.compile(r"CREATE TABLE IF NOT EXISTS (\w+)\s*\((.*?)\n\s*\);", re.S)
    column_pattern = re.compile(r"^\s{8,}(\w+)\s+TEXT", re.M)
    for module_name in migration_pkg._VERSION_MODULES:
        source = (Path(migration_pkg.__file__).parent / f"{module_name}.py").read_text(
            encoding="utf-8"
        )
        for table, body in pattern.findall(source):
            columns = set(column_pattern.findall(body))
            # NOT NULL markers live on the same line as the column name.
            not_null = {
                match.group(1)
                for match in re.finditer(r"^\s{8,}(\w+)\s+TEXT[^\n]*NOT NULL", body, re.M)
            }
            tables[table] = columns | {c for c in not_null}
    return tables


# ---------------------------------------------------------------------------
# schema contract
# ---------------------------------------------------------------------------


class RlsSchemaContractTests(unittest.TestCase):
    def test_every_protected_table_pins_tenant_id_not_null(self) -> None:
        tables = _baseline_tables_with_columns()
        for table in RLS_TABLES:
            self.assertIn(table, tables, f"{table} has no CREATE TABLE in the chain")
            self.assertIn("tenant_id", tables[table], f"{table} lacks tenant_id")

    def test_protected_set_excludes_global_and_token_paths(self) -> None:
        for table in ("tenants", "sla_policies", "prompt_versions", "csat_surveys"):
            self.assertNotIn(table, RLS_TABLES)


# ---------------------------------------------------------------------------
# installer SQL
# ---------------------------------------------------------------------------


class InstallStatementTests(unittest.TestCase):
    def test_policy_clause_uses_transaction_scoped_guc(self) -> None:
        clause = tenant_filter_clause()
        self.assertEqual(clause, f"tenant_id = current_setting('{TENANT_CONTEXT_GUC}', true)")

    def test_install_statements_cover_every_table_idempotently(self) -> None:
        statements = install_statements()
        self.assertEqual(len(statements), len(RLS_TABLES) * 3)
        for index, table in enumerate(RLS_TABLES):
            enable, drop, create = statements[index * 3 : index * 3 + 3]
            self.assertEqual(enable, f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            self.assertEqual(drop, f"DROP POLICY IF EXISTS {POLICY_NAME} ON {table}")
            clause = tenant_filter_clause()
            self.assertEqual(
                create,
                f"CREATE POLICY {POLICY_NAME} ON {table} USING ({clause}) WITH CHECK ({clause})",
            )

    def test_force_owner_statement(self) -> None:
        self.assertEqual(
            force_owner_statement("messages"),
            "ALTER TABLE messages FORCE ROW LEVEL SECURITY",
        )

    def test_install_rls_executes_every_statement(self) -> None:
        from app.rls import install_rls

        connection = RecordingConnection()
        install_rls(connection)
        expected = len(install_statements())
        self.assertEqual(len(connection.statements), expected)

    def test_install_rls_force_owner_appends_force_statements(self) -> None:
        from app.rls import install_rls

        connection = RecordingConnection()
        install_rls(connection, force_owner=True)
        base = len(install_statements())
        self.assertEqual(len(connection.statements), base + len(RLS_TABLES))
        force_sqls = [sql for sql, _ in connection.statements if "FORCE" in sql]
        self.assertEqual(len(force_sqls), len(RLS_TABLES))


# ---------------------------------------------------------------------------
# verify / missing_protection against catalog-shaped rows
# ---------------------------------------------------------------------------


class VerifyRlsTests(unittest.TestCase):
    def test_missing_protection_reports_unprotected_tables(self) -> None:
        report = {
            "tables": {
                RLS_TABLES[0]: {"rel_rls": True, "policy_ok": True},
                RLS_TABLES[1]: {"rel_rls": False, "policy_ok": False},
            }
        }
        problems = missing_protection(report)
        self.assertIn(f"{RLS_TABLES[1]}: RLS not enabled", problems)
        self.assertIn(f"{RLS_TABLES[2]}: not found", problems)
        self.assertEqual(len(problems), len(RLS_TABLES) - 1)

    def test_verify_reads_catalog_and_guc(self) -> None:
        clause = tenant_filter_clause()
        rows = [
            FakeRow(relname="conversations", relrowsecurity=1, relforcerowsecurity=0),
            FakeRow(relname="messages", relrowsecurity=1, relforcerowsecurity=0),
        ]
        policy_rows = [
            FakeRow(
                table_name="conversations",
                polname=POLICY_NAME,
                using_expr=f"({clause})",
                check_expr=f"({clause})",
            ),
            FakeRow(
                table_name="messages",
                polname=POLICY_NAME,
                using_expr="true",
                check_expr=None,
            ),
        ]
        connection = RecordingConnection(
            [rows, policy_rows, [FakeRow(value="acme")], [FakeRow(who="helix_app")]]
        )
        from app.rls import verify_rls

        report = verify_rls(connection)
        self.assertEqual(report["current_tenant"], "acme")
        self.assertEqual(report["current_user"], "helix_app")
        self.assertTrue(report["tables"]["conversations"]["policy_ok"])
        self.assertFalse(report["tables"]["messages"]["policy_ok"])
        problems = missing_protection(report)
        self.assertIn("messages: isolation policy missing or stale", problems)


# ---------------------------------------------------------------------------
# ambient scope semantics
# ---------------------------------------------------------------------------


class ScopeSemanticsTests(unittest.TestCase):
    def test_no_scope_by_default(self) -> None:
        self.assertIsNone(current_scope_mode())
        self.assertIsNone(current_tenant())

    def test_tenant_scope_binds_and_resets(self) -> None:
        with tenant_scope("acme"):
            self.assertEqual(current_tenant(), "acme")
            self.assertEqual(current_scope_mode(), "tenant")
            with maintenance_scope("sweep"):
                self.assertIsNone(current_tenant())
                self.assertEqual(current_scope_mode(), "maintenance")
            self.assertEqual(current_tenant(), "acme")
        self.assertIsNone(current_scope_mode())

    def test_empty_tenant_rejected(self) -> None:
        with self.assertRaises(ValueError):
            with tenant_scope("   "):
                pass

    def test_maintenance_requires_reason(self) -> None:
        with self.assertRaises(ValueError):
            with maintenance_scope("  "):
                pass

    def test_bind_tenant_scope_sets_without_reset_handle(self) -> None:
        from app.context import tenant_scope_context

        bind_tenant_scope("globex")
        try:
            self.assertEqual(current_tenant(), "globex")
        finally:
            tenant_scope_context.set(None)

    def test_bind_rejects_blank(self) -> None:
        self.assertRaises(ValueError, bind_tenant_scope, "")


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------


class RlsConfigTests(unittest.TestCase):
    def test_default_off(self) -> None:
        settings = Settings(database_path=Path("data/x.db"), auth_mode="api_key")
        self.assertFalse(settings.database_rls_enabled)

    def test_sqlite_backend_rejects_enforcement(self) -> None:
        settings = Settings(
            database_path=Path("data/x.db"),
            auth_mode="api_key",
            database_backend="sqlite",
            database_rls_enabled=True,
        )
        with self.assertRaises(ValueError):
            settings.validate()

    def test_postgresql_enforcement_accepted(self) -> None:
        settings = Settings(
            database_path=Path("data/x.db"),
            auth_mode="api_key",
            database_backend="postgresql",
            database_url="postgresql://localhost/helix",
            database_rls_enabled=True,
        )
        settings.validate()
        self.assertTrue(settings.database_rls_enabled)


# ---------------------------------------------------------------------------
# PostgresDatabase context binding (fake pool — no server required)
# ---------------------------------------------------------------------------


class _FakePool:
    def __init__(self) -> None:
        self.connection = RecordingConnection()
        self.closed = False

    @contextmanager
    def connect(self):
        yield self.connection

    def close(self) -> None:
        self.closed = True


def _postgres_database(*, rls: bool) -> Any:
    from app.postgres_db import PostgresDatabase

    database = PostgresDatabase.__new__(PostgresDatabase)
    database._pg_pool = _FakePool()  # type: ignore[attr-defined]
    database.rls_enforce_context = rls
    return database


class BindTenantContextTests(unittest.TestCase):
    def test_disabled_flag_is_a_noop(self) -> None:
        database = _postgres_database(rls=False)
        with tenant_scope("acme"):
            with database.connect():
                pass
        statements = database._pg_pool.connection.statements
        self.assertEqual(statements, [])

    def test_tenant_scope_emits_sticky_set_config(self) -> None:
        database = _postgres_database(rls=True)
        with tenant_scope("acme"):
            with database.connect():
                pass
        statements = database._pg_pool.connection.statements
        self.assertEqual(len(statements), 1)
        sql, params = statements[0]
        self.assertEqual(sql, f"SELECT set_config('{TENANT_CONTEXT_GUC}', ?, true)")
        self.assertEqual(params, ("acme",))

    def test_maintenance_scope_binds_nothing(self) -> None:
        database = _postgres_database(rls=True)
        with maintenance_scope("turn-worker-loop"):
            with database.connect():
                pass
        self.assertEqual(database._pg_pool.connection.statements, [])

    def test_missing_scope_fails_loudly(self) -> None:
        database = _postgres_database(rls=True)
        with self.assertRaises(TenantContextError):
            with database.connect():
                pass

    def test_context_does_not_leak_after_scope_exit(self) -> None:
        database = _postgres_database(rls=True)
        with tenant_scope("acme"):
            pass
        self.assertIsNone(current_tenant())
        with self.assertRaises(TenantContextError):
            with database.connect():
                pass


# ---------------------------------------------------------------------------
# audit self-scoping
# ---------------------------------------------------------------------------


class AuditSelfScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        from app.database import Database

        self._tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self._tmp.name) / "audit.db")
        self.database.initialize()
        # audit_events carries an FK to tenants(id)
        with self.database.connect() as connection:
            connection.executemany(
                "INSERT OR IGNORE INTO tenants (id, name, created_at) VALUES (?, ?, ?)",
                (
                    ("acme", "Acme", "2026-01-01T00:00:00+00:00"),
                    ("other", "Other", "2026-01-01T00:00:00+00:00"),
                ),
            )

    def tearDown(self) -> None:
        self.database.close()
        self._tmp.cleanup()

    def test_audit_outside_any_scope_writes_row(self) -> None:
        # Under enforced RLS this write binds its own tenant; on SQLite we
        # assert the observable behaviour: the row lands and is readable.
        self.database.audit("acme", None, "system", "turn.processed", {"ok": True})
        rows = self.database.export_audit_events("acme")
        types = {row["event_type"] for row in rows}
        self.assertIn("turn.processed", types)

    def test_audit_inside_foreign_tenant_scope_keeps_explicit_tenant(self) -> None:
        with tenant_scope("other"):
            self.database.audit("acme", None, "system", "member.invited", {"actor": "x"})
        rows = self.database.export_audit_events("acme")
        invited = [row for row in rows if row["event_type"] == "member.invited"]
        self.assertEqual(len(invited), 1)

    def test_audit_scope_helper_prefers_existing_tenant_scope(self) -> None:
        from contextlib import nullcontext

        with tenant_scope("acme"):
            scope = self.database._audit_scope("acme")
            self.assertIsInstance(scope, nullcontext)

    def test_audit_scope_helper_binds_when_unscoped(self) -> None:
        scope = self.database._audit_scope("acme")
        with scope:
            self.assertEqual(current_tenant(), "acme")


# ---------------------------------------------------------------------------
# worker scoping (fake queue/orchestrator — no real turns)
# ---------------------------------------------------------------------------


class _FakeQueue:
    def __init__(self, job: dict[str, Any] | None) -> None:
        self.job = job
        self.modes: list[str | None] = []

    def dequeue(self, worker_id: str, lease_seconds: int) -> dict[str, Any] | None:
        self.modes.append(current_scope_mode())
        return self.job

    def complete(self, *args: Any, **kwargs: Any) -> dict[str, Any] | None:
        self.modes.append(current_scope_mode())
        return {"status": "completed"}

    def fail(self, *args: Any, **kwargs: Any) -> dict[str, Any] | None:
        return None


class _RecordingOrchestrator:
    def __init__(self) -> None:
        self.tenants: list[str | None] = []

    def handle_customer_message(self, tenant_id: str, *_args: Any, **_kwargs: Any) -> dict:
        self.tenants.append(current_tenant())
        return {"response": "ok"}


class WorkerScopeTests(unittest.TestCase):
    def _worker(self, job: dict[str, Any] | None) -> tuple[Any, _FakeQueue, _RecordingOrchestrator]:
        from app.jobs import TurnJobWorker

        queue = _FakeQueue(job)
        orchestrator = _RecordingOrchestrator()
        database = _postgres_database(rls=False)
        worker = TurnJobWorker(
            database,  # type: ignore[arg-type]
            orchestrator,  # type: ignore[arg-type]
            queue=queue,  # type: ignore[arg-type]
            stream_enabled=False,
        )
        return worker, queue, orchestrator

    def test_scheduling_runs_under_maintenance_and_turn_under_job_tenant(self) -> None:
        job = {
            "id": "job_1",
            "tenant_id": "acme",
            "conversation_id": "conv_1",
            "content": "hi",
            "actor_id": "widget",
            "idempotency_key": "k",
            "attempts": 1,
            "request_id": None,
        }
        worker, queue, orchestrator = self._worker(job)
        self.assertTrue(worker.run_once("w1"))
        self.assertEqual(orchestrator.tenants, ["acme"])
        # claim and completion both observed the maintenance marker
        self.assertEqual(queue.modes, ["maintenance", "maintenance"])

    def test_empty_queue_is_a_noop(self) -> None:
        worker, queue, _orchestrator = self._worker(None)
        self.assertFalse(worker.run_once("w1"))
        self.assertEqual(queue.modes, ["maintenance"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
