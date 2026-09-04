"""Tests for M1 SSE token streaming (turn_job_chunks).

Covers the tokenizer, the database chunk persistence layer, the migration that
introduces the table, the worker's chunk-writing path (including idempotent
replay), and the SSE endpoint's ``event: token`` -> ``event: job`` stream.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from app.config import Settings
from app.database import Database
from app.jobs import split_stream_tokens
from app.main import create_app
from app.migrations import all_migrations, run_migrations

ADMIN_KEY = "stream-admin-key-0001"


class SplitStreamTokensTests(unittest.TestCase):
    def test_english_word_with_punctuation_kept_whole(self) -> None:
        self.assertEqual(split_stream_tokens("Hello, world!"), ["Hello,", " ", "world!"])

    def test_cjk_characters_split_single(self) -> None:
        self.assertEqual(split_stream_tokens("你好世界"), ["你", "好", "世", "界"])

    def test_mixed_english_and_cjk(self) -> None:
        self.assertEqual(split_stream_tokens("退款 refund"), ["退", "款", " ", "refund"])

    def test_whitespace_runs_collapse_to_single_space(self) -> None:
        self.assertEqual(split_stream_tokens("a   b"), ["a", " ", "b"])

    def test_leading_and_trailing_space_trimmed(self) -> None:
        self.assertEqual(split_stream_tokens("  hi  "), ["hi"])

    def test_empty_and_whitespace_only(self) -> None:
        self.assertEqual(split_stream_tokens(""), [])
        self.assertEqual(split_stream_tokens("   "), [])


class TurnJobChunkDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.db = Database(Path(self._tmp.name))
        self.db.initialize()
        self.db.ensure_tenant("t-ench")
        conv = self.db.create_conversation("t-ench", "Customer", None, "web", "admin", 120)
        self.conv_id = conv["id"]
        job = self.db.enqueue_turn_job(
            "t-ench", self.conv_id, "chunk-key-1", "actor-1", "content", 3
        )
        self.job_id = job[0]["id"]

    def tearDown(self) -> None:
        self.db.close()
        Path(self._tmp.name).unlink(missing_ok=True)

    def test_append_and_list_chunks_in_insertion_order(self) -> None:
        first = self.db.append_turn_job_chunk("t-ench", self.conv_id, self.job_id, "你")
        second = self.db.append_turn_job_chunk("t-ench", self.conv_id, self.job_id, "好")
        self.assertEqual(int(first["seq"]), 1)
        self.assertEqual(int(second["seq"]), 2)
        chunks = self.db.list_turn_job_chunks("t-ench", self.job_id)
        self.assertEqual([c["content"] for c in chunks], ["你", "好"])

    def test_after_seq_resumable_polling(self) -> None:
        for token in ["a", "b", "c"]:
            self.db.append_turn_job_chunk("t-ench", self.conv_id, self.job_id, token)
        chunks = self.db.list_turn_job_chunks("t-ench", self.job_id, after_seq=1)
        self.assertEqual([c["content"] for c in chunks], ["b", "c"])
        self.assertTrue(all(int(c["seq"]) > 1 for c in chunks))

    def test_after_seq_clamps_negative_to_zero(self) -> None:
        self.db.append_turn_job_chunk("t-ench", self.conv_id, self.job_id, "x")
        chunks = self.db.list_turn_job_chunks("t-ench", self.job_id, after_seq=-5)
        self.assertEqual(len(chunks), 1)

    def test_append_rejects_empty_content(self) -> None:
        with self.assertRaises(ValueError):
            self.db.append_turn_job_chunk("t-ench", self.conv_id, self.job_id, "")

    def test_chunks_are_scoped_by_tenant_and_job(self) -> None:
        self.db.append_turn_job_chunk("t-ench", self.conv_id, self.job_id, "only")
        self.assertEqual(self.db.list_turn_job_chunks("t-ench", "no-such-job"), [])
        self.assertEqual(self.db.list_turn_job_chunks("other-tenant", self.job_id), [])


class Migration005Tests(unittest.TestCase):
    def test_turn_job_chunks_table_created_by_migration_5(self) -> None:
        """A DB created before version 5 gets the table, index, and seq trigger."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = Path(f.name)
        try:
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            connection.executescript(
                """
                CREATE TABLE tenants (id TEXT PRIMARY KEY, name TEXT NOT NULL,
                    created_at TEXT NOT NULL);
                CREATE TABLE conversations (
                    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
                    customer_name TEXT NOT NULL, channel TEXT NOT NULL,
                    status TEXT NOT NULL, priority TEXT NOT NULL DEFAULT 'normal',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE turn_jobs (
                    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL, content TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE schema_migrations (
                    version INTEGER PRIMARY KEY,
                    description TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                );
                """
            )
            # Mark migrations 1-4 as already applied so the 1.1 migrations (5,
            # 6, 7, 8, and 9) run when upgrading from a 1.0 database.
            for version, description in ((1, "b"), (2, "b"), (3, "b"), (4, "b")):
                connection.execute(
                    "INSERT INTO schema_migrations (version, description, applied_at) "
                    "VALUES (?, ?, ?)",
                    (version, description, "2026-01-01T00:00:00+00:00"),
                )
            connection.commit()
            connection.close()

            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            result = run_migrations(connection, all_migrations())
            connection.close()
            self.assertEqual(
                result.applied,
                [
                    5,
                    6,
                    7,
                    8,
                    9,
                    10,
                    11,
                    12,
                    13,
                    14,
                    15,
                    16,
                    17,
                    18,
                    19,
                    20,
                    21,
                    22,
                    23,
                    24,
                    25,
                    26,
                    27,
                    28,
                    29,
                    30,
                    31,
                    32,
                    33,
                    34,
                    35,
                    36,
                    37,
                    38,
                    39,
                    40,
                    41,
                    42,
                    43,
                    44,
                ],
            )

            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            tables = {
                r[0]
                for r in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            self.assertIn("turn_job_chunks", tables)
            indexes = {
                r[0]
                for r in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                ).fetchall()
            }
            self.assertIn("idx_turn_job_chunks_job", indexes)
            triggers = {
                r[0]
                for r in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger'"
                ).fetchall()
            }
            self.assertIn("turn_job_chunks_seq_fill", triggers)

            # The trigger fills seq on real inserts.  Use a raw connection so
            # the pre-5 schema (which predates later turn_jobs columns) is not
            # re-created by Database.initialize()'s full executescript.
            # Columns added by later migrations (tenant policy, webhooks) are
            # declared explicitly so the INSERT survives them.
            connection.execute(
                "INSERT INTO tenants (id, name, created_at) "
                "VALUES ('t', 'T', '2026-01-01T00:00:00+00:00')"
            )
            connection.execute(
                "INSERT INTO conversations (id, tenant_id, customer_name, channel, status, "
                "created_at, updated_at) VALUES ('c','t','C','web','open',"
                "'2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00')"
            )
            connection.execute(
                "INSERT INTO turn_jobs (id, tenant_id, conversation_id, content, created_at, "
                "updated_at) VALUES ('j1','t','c','h','2026-01-01T00:00:00+00:00',"
                "'2026-01-01T00:00:00+00:00')"
            )
            connection.execute(
                "INSERT INTO turn_job_chunks (id, tenant_id, conversation_id, job_id, seq, "
                "content, created_at) VALUES ('chk1','t','c','j1',0,'你',"
                "'2026-01-01T00:00:00+00:00')"
            )
            chunk = connection.execute(
                "SELECT seq FROM turn_job_chunks WHERE id = 'chk1'"
            ).fetchone()
            self.assertEqual(int(chunk["seq"]), 1)
            connection.commit()
            connection.close()
        finally:
            path.unlink(missing_ok=True)


class StreamWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "test.db"
        principals = {ADMIN_KEY: {"tenant_id": "t1", "actor_id": "agent.admin", "role": "admin"}}
        self.settings = Settings(
            database_path=self.db_path,
            auth_mode="api_key",
            api_keys_json=json.dumps(principals),
            rate_limit_per_minute=1000,
            docs_enabled=False,
            turn_job_stream_enabled=True,
            turn_job_stream_pacing_ms=0,
            enable_llm=False,
        )
        self.client = TestClient(create_app(self.settings))
        self.headers = {"X-API-Key": ADMIN_KEY, "X-Tenant-Id": "t1"}
        self.services = cast_services(self.client)

    def tearDown(self) -> None:
        self.client.close()
        self.services.database.close()
        self._tmp.cleanup()

    def create_conversation(self, name: str = "Stream Customer") -> dict:
        response = self.client.post(
            "/api/conversations",
            json={"customer_name": name, "channel": "web"},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def _parse_sse(self, text: str) -> list[tuple[str, dict]]:
        """Parse an SSE body into (event, data) tuples."""
        events: list[tuple[str, dict]] = []
        current_event = ""
        current_data: list[str] = []
        for line in text.splitlines():
            if line.startswith("event: "):
                current_event = line[len("event: ") :]
            elif line.startswith("data: "):
                current_data.append(line[len("data: ") :])
            elif line == "":
                if current_data:
                    events.append((current_event, json.loads("\n".join(current_data))))
                current_event = ""
                current_data = []
        return events

    def test_sse_stream_emits_tokens_then_completed_job(self) -> None:
        conversation = self.create_conversation()
        job = self.client.post(
            f"/api/conversations/{conversation['id']}/turn-jobs",
            json={"content": "帮我查一下我的订单"},
            headers={**self.headers, "Idempotency-Key": "sse-token-0001"},
        )
        self.assertEqual(job.status_code, 202, job.text)
        self.services.turn_worker.run_once("sse-token-worker")
        events = self.client.get(
            f"/api/turn-jobs/{job.json()['id']}/events?timeout=2",
            headers=self.headers,
        )
        self.assertEqual(events.status_code, 200, events.text)
        self.assertIn("text/event-stream", events.headers["content-type"])

        parsed = self._parse_sse(events.text)
        token_events = [(name, payload) for name, payload in parsed if name == "token"]
        job_events = [(name, payload) for name, payload in parsed if name == "job"]
        self.assertTrue(token_events, "expected at least one token event")
        self.assertTrue(job_events, "expected a terminal job event")

        # Tokens must arrive in monotonic seq order and, when concatenated,
        # reproduce the assistant reply (with whitespace normalized).
        seqs = [int(payload["seq"]) for _, payload in token_events]
        self.assertEqual(seqs, sorted(seqs))
        streamed = "".join(payload["content"] for _, payload in token_events)
        assistant = next(
            m
            for m in self.services.database.list_messages("t1", conversation["id"])
            if m["role"] == "assistant"
        )
        self.assertEqual(streamed, " ".join(assistant["content"].split()))

        # The token events must precede the terminal job event in the stream.
        token_indexes = [index for index, (name, _) in enumerate(parsed) if name == "token"]
        job_index = next(index for index, (name, _) in enumerate(parsed) if name == "job")
        self.assertTrue(all(index < job_index for index in token_indexes))
        self.assertEqual(job_events[0][1]["status"], "completed")

    def test_idempotent_replay_does_not_duplicate_chunks(self) -> None:
        conversation = self.create_conversation()
        key = "sse-replay-0001"
        first = self.client.post(
            f"/api/conversations/{conversation['id']}/turn-jobs",
            json={"content": "订单丢了"},
            headers={**self.headers, "Idempotency-Key": key},
        )
        self.assertEqual(first.status_code, 202, first.text)
        job_id = first.json()["id"]

        # Process once: chunks written, job completed.
        self.services.turn_worker.run_once("sse-replay-worker")
        first_chunks = self.services.database.list_turn_job_chunks("t1", job_id)
        self.assertTrue(first_chunks, "expected chunks after the first run")

        # Re-submitting the same idempotency key returns the same job as a replay.
        replay = self.client.post(
            f"/api/conversations/{conversation['id']}/turn-jobs",
            json={"content": "订单丢了"},
            headers={**self.headers, "Idempotency-Key": key},
        )
        self.assertEqual(replay.status_code, 202, replay.text)
        self.assertEqual(replay.json()["id"], job_id)
        self.assertEqual(replay.headers["X-Idempotent-Replay"], "true")

        # A second processing pass must not append any new chunks.
        self.services.turn_worker.run_once("sse-replay-worker")
        second_chunks = self.services.database.list_turn_job_chunks("t1", job_id)
        self.assertEqual(
            [(c["seq"], c["content"]) for c in second_chunks],
            [(c["seq"], c["content"]) for c in first_chunks],
        )

    def test_write_stream_chunks_skips_idempotent_replay_response(self) -> None:
        """The chunk writer must not persist tokens for a cached replay response.

        The worker calls ``_write_stream_chunks`` for every processed job; when
        ``handle_customer_message`` returns an idempotent replay (the turn was
        already completed on an earlier run), the chunks were written on that
        first run and must not be duplicated.
        """
        worker = self.services.turn_worker
        conversation = self.create_conversation("Replay Guard")
        job = self.services.database.enqueue_turn_job(
            "t1", conversation["id"], "replay-guard-key", "agent.admin", "退款", 3
        )
        job_id = job[0]["id"]
        worker._write_stream_chunks(
            {"tenant_id": "t1", "conversation_id": conversation["id"], "id": job_id},
            {"idempotent_replay": True, "assistant_message": {"content": "no stream"}},
        )
        self.assertEqual(worker.database.list_turn_job_chunks("t1", job_id), [])

        # A normal (non-replay) response does write chunks for the same job.
        worker._write_stream_chunks(
            {"tenant_id": "t1", "conversation_id": conversation["id"], "id": job_id},
            {"idempotent_replay": False, "assistant_message": {"content": "你好"}},
        )
        chunks = worker.database.list_turn_job_chunks("t1", job_id)
        self.assertEqual([c["content"] for c in chunks], ["你", "好"])


def cast_services(client: TestClient) -> Any:
    app = cast(Any, client.app)
    return app.state.services


if __name__ == "__main__":
    unittest.main()
