"""Tests for cross-region async replication."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from app.region_replication import (
    ReplicationLog,
    ReplicationWorker,
)


class TestReplicationLog(unittest.TestCase):
    def setUp(self) -> None:
        from app.database import Database

        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db.close()
        self.database = Database(Path(self.temp_db.name))
        self.database.initialize()

        # Run migration v43 to create replication_log table
        from app.migrations.v43_replication_log import migrate as migrate_v43
        with self.database.connect() as conn:
            migrate_v43(conn)
            # replication_log.tenant_id references tenants(id)
            conn.execute(
                "INSERT OR IGNORE INTO tenants (id, name, created_at) "
                "VALUES ('tenant-1', 'Test Tenant', '2026-01-01T00:00:00Z')"
            )

        self.log = ReplicationLog(self.database)

    def tearDown(self) -> None:
        import os

        try:
            self.database.close()
        except Exception:
            pass
        try:
            os.unlink(self.temp_db.name)
        except OSError:
            pass

    def test_record_change(self) -> None:
        entry_id = self.log.record_change(
            tenant_id="tenant-1",
            source_region="us-east-1",
            target_region="us-west-2",
            table_name="conversations",
            row_id="conv-123",
            operation="insert",
            payload={"id": "conv-123", "status": "active"},
        )

        self.assertIsNotNone(entry_id)
        self.assertTrue(entry_id.startswith("repl-"))

    def test_get_pending_entries(self) -> None:
        self.log.record_change(
            tenant_id="tenant-1",
            source_region="us-east-1",
            target_region="us-west-2",
            table_name="conversations",
            row_id="conv-123",
            operation="insert",
            payload={"id": "conv-123"},
        )
        self.log.record_change(
            tenant_id="tenant-1",
            source_region="us-east-1",
            target_region="eu-central-1",
            table_name="conversations",
            row_id="conv-456",
            operation="update",
            payload={"id": "conv-456"},
        )

        entries = self.log.get_pending_entries("us-west-2")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].row_id, "conv-123")
        self.assertEqual(entries[0].operation, "insert")

    def test_get_pending_entries_limit(self) -> None:
        for i in range(10):
            self.log.record_change(
                tenant_id="tenant-1",
                source_region="us-east-1",
                target_region="us-west-2",
                table_name="conversations",
                row_id=f"conv-{i}",
                operation="insert",
                payload={"id": f"conv-{i}"},
            )

        entries = self.log.get_pending_entries("us-west-2", limit=5)
        self.assertEqual(len(entries), 5)

    def test_mark_replicated(self) -> None:
        entry_id = self.log.record_change(
            tenant_id="tenant-1",
            source_region="us-east-1",
            target_region="us-west-2",
            table_name="conversations",
            row_id="conv-123",
            operation="insert",
            payload={"id": "conv-123"},
        )

        self.log.mark_replicated(entry_id)

        entries = self.log.get_pending_entries("us-west-2")
        self.assertEqual(len(entries), 0)

    def test_count_pending(self) -> None:
        for i in range(3):
            self.log.record_change(
                tenant_id="tenant-1",
                source_region="us-east-1",
                target_region="us-west-2",
                table_name="conversations",
                row_id=f"conv-{i}",
                operation="insert",
                payload={"id": f"conv-{i}"},
            )

        count = self.log.count_pending("us-west-2")
        self.assertEqual(count, 3)

    def test_operations(self) -> None:
        self.log.record_change(
            tenant_id="tenant-1",
            source_region="us-east-1",
            target_region="us-west-2",
            table_name="conversations",
            row_id="conv-123",
            operation="insert",
            payload={"id": "conv-123"},
        )
        self.log.record_change(
            tenant_id="tenant-1",
            source_region="us-east-1",
            target_region="us-west-2",
            table_name="conversations",
            row_id="conv-123",
            operation="update",
            payload={"id": "conv-123", "status": "resolved"},
        )
        self.log.record_change(
            tenant_id="tenant-1",
            source_region="us-east-1",
            target_region="us-west-2",
            table_name="conversations",
            row_id="conv-456",
            operation="delete",
            payload={"id": "conv-456"},
        )

        entries = self.log.get_pending_entries("us-west-2")
        self.assertEqual(len(entries), 3)
        operations = [e.operation for e in entries]
        self.assertIn("insert", operations)
        self.assertIn("update", operations)
        self.assertIn("delete", operations)


class TestReplicationWorker(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        from app.database import Database

        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db.close()
        self.database = Database(Path(self.temp_db.name))
        self.database.initialize()

        # Run migration v43 to create replication_log table
        from app.migrations.v43_replication_log import migrate as migrate_v43
        with self.database.connect() as conn:
            migrate_v43(conn)
            # replication_log.tenant_id references tenants(id)
            conn.execute(
                "INSERT OR IGNORE INTO tenants (id, name, created_at) "
                "VALUES ('tenant-1', 'Test Tenant', '2026-01-01T00:00:00Z')"
            )

        self.log = ReplicationLog(self.database)
        self.worker = ReplicationWorker(
            database=self.database,
            replication_log=self.log,
            target_region="us-west-2",
            target_base_url="http://us-west-2.example.com",
            batch_size=10,
        )

    def tearDown(self) -> None:
        import os

        try:
            self.database.close()
        except Exception:
            pass
        try:
            os.unlink(self.temp_db.name)
        except OSError:
            pass

    async def test_replicate_batch_empty(self) -> None:
        stats = await self.worker.replicate_batch()
        self.assertEqual(stats.entries_processed, 0)
        self.assertEqual(stats.entries_succeeded, 0)
        self.assertEqual(stats.entries_failed, 0)

    async def test_replicate_batch_success(self) -> None:
        self.log.record_change(
            tenant_id="tenant-1",
            source_region="us-east-1",
            target_region="us-west-2",
            table_name="conversations",
            row_id="conv-123",
            operation="insert",
            payload={"id": "conv-123"},
        )

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = lambda: None

        with patch("httpx.AsyncClient.post", return_value=mock_response):
            stats = await self.worker.replicate_batch()

        self.assertEqual(stats.entries_processed, 1)
        self.assertEqual(stats.entries_succeeded, 1)
        self.assertEqual(stats.entries_failed, 0)

        count = self.log.count_pending("us-west-2")
        self.assertEqual(count, 0)

    async def test_replicate_batch_partial_failure(self) -> None:
        for i in range(3):
            self.log.record_change(
                tenant_id="tenant-1",
                source_region="us-east-1",
                target_region="us-west-2",
                table_name="conversations",
                row_id=f"conv-{i}",
                operation="insert",
                payload={"id": f"conv-{i}"},
            )

        call_count = 0

        async def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_response = AsyncMock()
            if call_count == 2:
                mock_response.status_code = 503
                mock_response.raise_for_status = lambda: (_ for _ in ()).throw(
                    httpx.HTTPStatusError(
                        "Service unavailable",
                        request=AsyncMock(),
                        response=mock_response,
                    )
                )
            else:
                mock_response.status_code = 200
                mock_response.raise_for_status = lambda: None
            return mock_response

        with patch("httpx.AsyncClient.post", side_effect=mock_post):
            stats = await self.worker.replicate_batch()

        self.assertEqual(stats.entries_processed, 3)
        self.assertEqual(stats.entries_succeeded, 2)
        self.assertEqual(stats.entries_failed, 1)

        count = self.log.count_pending("us-west-2")
        self.assertEqual(count, 1)

    async def test_replicate_batch_respects_limit(self) -> None:
        for i in range(20):
            self.log.record_change(
                tenant_id="tenant-1",
                source_region="us-east-1",
                target_region="us-west-2",
                table_name="conversations",
                row_id=f"conv-{i}",
                operation="insert",
                payload={"id": f"conv-{i}"},
            )

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = lambda: None

        with patch("httpx.AsyncClient.post", return_value=mock_response):
            stats = await self.worker.replicate_batch()

        self.assertEqual(stats.entries_processed, 10)
        self.assertEqual(stats.entries_succeeded, 10)

        count = self.log.count_pending("us-west-2")
        self.assertEqual(count, 10)


if __name__ == "__main__":
    unittest.main()
