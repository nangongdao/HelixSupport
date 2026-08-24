"""ROADMAP 18.5 PostgreSQL dense-message search fast-path tests.

The production path is PostgreSQL-only, but its SQL is intentionally shared
SQL.  These tests execute it against SQLite with message FTS disabled, then
compare it with the existing aggregate fallback.  Live PostgreSQL tests pin
the supporting native indexes separately in ``tests.test_postgres``.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from app.database import Database
from scripts.pagination_load_test import (
    SELECTIVE_MESSAGE_SEARCH_TERM,
    validate_message_search_probe,
)


class ConversationSearchWindowTests(unittest.TestCase):
    TENANT = "window-tenant"
    OTHER_TENANT = "window-other"

    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.database = Database(Path(self._temporary_directory.name) / "search-window.db")
        self.database.initialize()
        self.database.ensure_tenant(self.TENANT)
        self.database.ensure_tenant(self.OTHER_TENANT)
        self.conversation_ids: list[str] = []

        for index in range(8):
            conversation = self.database.create_conversation(
                self.TENANT,
                f"Window Customer {index}",
                f"WINDOW-{index}",
                "web",
                "admin",
                120,
            )
            self.conversation_ids.append(str(conversation["id"]))
            suffix = " sparse-only" if index < 2 else ""
            self.database.add_message(
                self.TENANT,
                str(conversation["id"]),
                "customer",
                "Customer",
                f"dense-message {index}{suffix}",
            )
            with self.database.connect() as connection:
                connection.execute(
                    "UPDATE conversations SET updated_at = ? WHERE tenant_id = ? AND id = ?",
                    (f"2026-08-18T00:00:{index:02d}+00:00", self.TENANT, conversation["id"]),
                )

        other = self.database.create_conversation(
            self.OTHER_TENANT, "Other Customer", "OTHER-1", "web", "admin", 120
        )
        self.database.add_message(
            self.OTHER_TENANT,
            str(other["id"]),
            "customer",
            "Other",
            "other-tenant-only",
        )
        # PostgreSQL has no SQLite message_fts mirror and therefore enters the
        # LIKE/pg_trgm branch.  Toggling only this capability and ``backend``
        # preserves the exact shared query implementation under test.
        self.database._message_fts_enabled = False

    def tearDown(self) -> None:
        self.database.close()
        self._temporary_directory.cleanup()

    def _ids(self, backend: str, **kwargs: Any) -> list[str]:
        self.database.backend = backend
        return [
            str(row["id"])
            for row in self.database.list_conversations(self.TENANT, sort="updated", **kwargs)
        ]

    def _run_fast_path(self, **kwargs: Any) -> tuple[list[str], list[Any]]:
        captured: list[Any] = []
        original = self.database._query_conversations_windowed

        def capture(*args: Any, **inner_kwargs: Any) -> Any:
            result = original(*args, **inner_kwargs)
            captured.append(result)
            return result

        with patch.object(self.database, "_query_conversations_windowed", side_effect=capture):
            ids = self._ids("postgresql", **kwargs)
        return ids, captured

    def test_dense_match_returns_same_offset_page_without_fallback(self) -> None:
        expected = self._ids("sqlite", search="dense-message", limit=3, offset=2)

        actual, captured = self._run_fast_path(search="dense-message", limit=3, offset=2)

        self.assertEqual(actual, expected)
        self.assertEqual(len(captured), 1)
        self.assertIsNotNone(captured[0], "dense search unexpectedly used the aggregate fallback")

    def test_sparse_match_falls_back_without_losing_older_results(self) -> None:
        expected = self._ids("sqlite", search="sparse-only", limit=2)

        actual, captured = self._run_fast_path(search="sparse-only", limit=2)

        self.assertEqual(actual, expected)
        self.assertEqual(set(actual), set(self.conversation_ids[:2]))
        self.assertEqual(captured, [None])

    def test_updated_cursor_page_matches_aggregate_query(self) -> None:
        first = self.database.list_conversations(
            self.TENANT, search="dense-message", sort="updated", limit=2
        )
        last = first[-1]
        cursor = ("updated", None, None, str(last["updated_at"]), str(last["id"]))
        expected = self._ids("sqlite", search="dense-message", limit=2, cursor=cursor)

        actual, captured = self._run_fast_path(search="dense-message", limit=2, cursor=cursor)

        self.assertEqual(actual, expected)
        self.assertIsNotNone(captured[0])
        self.assertFalse(set(actual) & {str(row["id"]) for row in first})

    def test_message_search_never_crosses_tenant_boundary(self) -> None:
        expected = self._ids("sqlite", search="other-tenant-only", limit=3)

        actual, captured = self._run_fast_path(search="other-tenant-only", limit=3)

        self.assertEqual(expected, [])
        self.assertEqual(actual, [])
        self.assertEqual(captured, [None])

    def test_capacity_probe_requires_a_message_only_marker(self) -> None:
        self.database.add_message(
            self.TENANT,
            self.conversation_ids[0],
            "customer",
            "Customer",
            SELECTIVE_MESSAGE_SEARCH_TERM,
        )
        self.assertEqual(validate_message_search_probe(self.database, self.TENANT), 1)

        with self.database.connect() as connection:
            connection.execute(
                "UPDATE conversations SET customer_name = ? WHERE tenant_id = ? AND id = ?",
                (SELECTIVE_MESSAGE_SEARCH_TERM, self.TENANT, self.conversation_ids[0]),
            )
        with self.assertRaisesRegex(RuntimeError, "zero conversation-field hits"):
            validate_message_search_probe(self.database, self.TENANT)


if __name__ == "__main__":
    unittest.main()
