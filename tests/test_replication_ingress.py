"""Tests for the cross-cell replication ingress (ROADMAP 2.2.2).

Two layers, because the ingress used to be covered on one backend only:

* The SQLite contract, which runs always.
* The same contract against a live PostgreSQL instance
  (``HELIX_PG_INTEGRATION=1`` + ``DATABASE_URL``), because the ingress shipped
  with SQLite's ``INSERT OR REPLACE`` — a statement PostgreSQL rejects outright
  *and* that is destructive on SQLite (REPLACE deletes the conflicting row and
  re-inserts it, so every column the source never sent reverts to its default).
  A SQLite-only test run can see neither half of that.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

CELL_SECRET = "smoke-cell-secret-32bytes-long-0001"
OTHER_TENANT = "repl-other-tenant"


def _cell_registry() -> dict[str, dict[str, str]]:
    return {
        "cell-default": {
            "db_url": "sqlite:///data/default.db",
            "redis_url": "redis://localhost:6379/0",
            "health_url": "http://127.0.0.1:8000/health",
            "region": "us-east-1",
            "capacity_tier": "default",
        },
        "cell-premium": {
            "db_url": "sqlite:///data/premium.db",
            "redis_url": "redis://localhost:6380/0",
            "health_url": "http://127.0.0.1:8001/health",
            "region": "us-west-2",
            "capacity_tier": "premium",
        },
    }


def _build_app() -> TestClient:
    tmp = tempfile.mkdtemp()
    settings = Settings(
        database_path=Path(tmp) / "cell.db",
        widget_secret="smoke-widget-secret",
        widget_frame_ancestors=("'self'",),
        cell_registry_config=_cell_registry(),
        current_cell_id="cell-default",
        control_plane_secret=CELL_SECRET,
    )
    return TestClient(create_app(settings))


def _build_postgres_app() -> TestClient:
    tmp = tempfile.mkdtemp()
    settings = Settings(
        database_backend="postgresql",
        database_url=os.environ["DATABASE_URL"],
        database_path=Path(tmp) / "unused.db",
        widget_secret="smoke-widget-secret",
        widget_frame_ancestors=("'self'",),
        cell_registry_config=_cell_registry(),
        current_cell_id="cell-default",
        control_plane_secret=CELL_SECRET,
    )
    return TestClient(create_app(settings))


class _ReplicationApplyContract:
    """What the ingress must do on *every* backend.

    Mixed into each concrete backend class so the PostgreSQL case cannot quietly
    drift away from the SQLite one.
    """

    client: TestClient

    def _services(self) -> Any:
        return cast(Any, self.client.app).state.services

    def _apply(self, payload: dict[str, Any]) -> Any:
        return self.client.post(
            "/api/internal/replication/apply",
            headers={"X-Internal-Token": CELL_SECRET},
            json=payload,
        )

    def _seed_target_row(self, row_id: str, tenant_id: str = "demo") -> None:
        """Put a fully-populated conversation on the receiving cell.

        ``status``/``version``/``last_message_at``/``first_response_at`` are the
        target's own operational state: they are derived locally and are *not*
        on the replication whitelist, so a replicated change must leave them
        alone.
        """
        database = self._services().database
        database.ensure_tenant(tenant_id)
        with database.connect() as conn:
            conn.execute(
                "INSERT INTO conversations (id, tenant_id, customer_name, status,"
                " preview, channel, version, last_message_at, first_response_at,"
                " created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row_id,
                    tenant_id,
                    "Acme",
                    "resolved",
                    "the real preview",
                    "web",
                    7,
                    "2026-09-01T10:00:00Z",
                    "2026-09-01T09:00:00Z",
                    "2026-08-01T00:00:00Z",
                    "2026-09-01T10:00:00Z",
                ),
            )

    def _row(self, row_id: str) -> dict[str, Any]:
        with self._services().database.connect() as conn:
            row = conn.execute("SELECT * FROM conversations WHERE id = ?", (row_id,)).fetchone()
        assert row is not None, f"conversation {row_id} is missing"
        return dict(row)

    def test_update_keeps_columns_the_source_did_not_send(self) -> None:
        """A replicated update must not wipe target-side operational state."""
        self._seed_target_row("conv-repl-1")

        response = self._apply(
            {
                "table_name": "conversations",
                "row_id": "conv-repl-1",
                "operation": "update",
                "tenant_id": "demo",
                "payload": {"customer_name": "Acme Renamed"},
            }
        )
        self.assertEqual(response.status_code, 200)

        row = self._row("conv-repl-1")
        self.assertEqual(row["customer_name"], "Acme Renamed")
        self.assertEqual(row["status"], "resolved")
        self.assertEqual(row["version"], 7)
        self.assertEqual(row["preview"], "the real preview")
        self.assertEqual(row["last_message_at"], "2026-09-01T10:00:00Z")
        self.assertEqual(row["first_response_at"], "2026-09-01T09:00:00Z")
        self.assertEqual(row["created_at"], "2026-08-01T00:00:00Z")
        self.assertEqual(row["updated_at"], "2026-09-01T10:00:00Z")

    def test_synthetic_defaults_are_insert_only(self) -> None:
        """Placeholder defaults must not overwrite real values on a conflict.

        ``_REPLICATED_SYNTHETIC`` exists so a first insert cannot die on a NOT
        NULL constraint.  As part of a conflict update it would reset
        ``status`` to 'open', ``channel`` to 'replicated' and the customer name
        to 'Replicated' on every replicated change.
        """
        self._seed_target_row("conv-repl-2")

        response = self._apply(
            {
                "table_name": "conversations",
                "row_id": "conv-repl-2",
                "operation": "update",
                "tenant_id": "demo",
                "payload": {"preview": "replicated preview"},
            }
        )
        self.assertEqual(response.status_code, 200)

        row = self._row("conv-repl-2")
        self.assertEqual(row["preview"], "replicated preview")
        self.assertEqual(row["status"], "resolved")
        self.assertEqual(row["channel"], "web")
        self.assertEqual(row["customer_name"], "Acme")

    def test_fresh_row_still_gets_synthetic_defaults(self) -> None:
        """The insert path keeps working: a brand-new row must satisfy NOT NULL."""
        response = self._apply(
            {
                "table_name": "conversations",
                "row_id": "conv-repl-new",
                "operation": "insert",
                "tenant_id": "demo",
                "payload": {"preview": "first sighting"},
            }
        )
        self.assertEqual(response.status_code, 200)

        row = self._row("conv-repl-new")
        self.assertEqual(row["preview"], "first sighting")
        self.assertEqual(row["status"], "open")
        self.assertEqual(row["channel"], "replicated")
        self.assertEqual(row["customer_name"], "Replicated")

    def test_conflict_on_another_tenants_row_is_refused(self) -> None:
        """A peer must not be able to rename a row into its own tenant.

        The row id is the primary key, so an unguarded upsert lets a replicated
        payload take over a conversation owned by a different tenant.  The
        apply is refused (409) and the owner's row is untouched.
        """
        self._seed_target_row("conv-repl-3", tenant_id="demo")

        response = self._apply(
            {
                "table_name": "conversations",
                "row_id": "conv-repl-3",
                "operation": "update",
                "tenant_id": OTHER_TENANT,
                "payload": {"customer_name": "Stolen"},
            }
        )
        self.assertEqual(response.status_code, 409)

        row = self._row("conv-repl-3")
        self.assertEqual(row["tenant_id"], "demo")
        self.assertEqual(row["customer_name"], "Acme")
        self.assertEqual(row["status"], "resolved")


class TestReplicationIngress(unittest.TestCase, _ReplicationApplyContract):
    def setUp(self) -> None:
        self.client = _build_app()

    def tearDown(self) -> None:
        self.client.close()

    def test_apply_requires_internal_token(self) -> None:
        response = self.client.post(
            "/api/internal/replication/apply",
            json={
                "table_name": "conversations",
                "row_id": "conv-1",
                "operation": "insert",
                "tenant_id": "demo",
                "payload": {"customer_name": "A"},
            },
        )
        self.assertEqual(response.status_code, 401)

    def test_apply_insert_with_synthetic_defaults(self) -> None:
        response = self._apply(
            {
                "table_name": "conversations",
                "row_id": "conv-2",
                "operation": "insert",
                "tenant_id": "demo",
                "payload": {"customer_name": "Replicated", "preview": "p"},
            }
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "applied")

    def test_apply_rejects_unsupported_table(self) -> None:
        response = self._apply(
            {
                "table_name": "evil_table",
                "row_id": "x",
                "operation": "insert",
                "tenant_id": "demo",
                "payload": {"customer_name": "A"},
            }
        )
        self.assertEqual(response.status_code, 400)

    def test_apply_rejects_invalid_operation(self) -> None:
        response = self._apply(
            {
                "table_name": "conversations",
                "row_id": "x",
                "operation": "drop",
                "tenant_id": "demo",
                "payload": {},
            }
        )
        self.assertEqual(response.status_code, 400)

    def test_apply_delete(self) -> None:
        response = self._apply(
            {
                "table_name": "conversations",
                "row_id": "conv-3",
                "operation": "delete",
                "tenant_id": "demo",
                "payload": {},
            }
        )
        self.assertEqual(response.status_code, 200)

    def test_apply_wrong_token_rejected(self) -> None:
        response = self.client.post(
            "/api/internal/replication/apply",
            headers={"X-Internal-Token": "wrong"},
            json={
                "table_name": "conversations",
                "row_id": "x",
                "operation": "insert",
                "tenant_id": "demo",
                "payload": {"customer_name": "A"},
            },
        )
        self.assertEqual(response.status_code, 401)


@unittest.skipUnless(
    os.getenv("HELIX_PG_INTEGRATION") and os.getenv("DATABASE_URL"),
    "Requires PostgreSQL instance and HELIX_PG_INTEGRATION=1",
)
class TestReplicationIngressPostgres(unittest.TestCase, _ReplicationApplyContract):
    """The same contract on the production backend."""

    @classmethod
    def setUpClass(cls) -> None:
        import psycopg2

        connection = psycopg2.connect(os.environ["DATABASE_URL"])
        connection.autocommit = True
        connection.cursor().execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        connection.close()

    def setUp(self) -> None:
        self.client = _build_postgres_app()

    def tearDown(self) -> None:
        self.client.close()


if __name__ == "__main__":
    unittest.main()
