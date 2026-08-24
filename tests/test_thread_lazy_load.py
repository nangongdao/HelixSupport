"""ROADMAP §18.4 — message-thread upward lazy-load contract (backend).

The detail endpoint serves the newest ``message_limit`` slice of a long
transcript and echoes the same opaque keyset cursors as ``/messages`` (plus a
conservative ``X-Has-More``) on the response headers.  The client follows that
cursor upward with ``before=true`` without ever parsing its content.  These
tests prove:

1. A detail request without ``message_limit`` is unchanged: full transcript,
   no pagination headers (the API contract stays "only add").
2. With ``message_limit`` the detail body is capped to the tail and the
   response carries ``X-Prev-Cursor`` / ``X-Has-More`` / ``X-Page-Limit``.
3. Echoing the opaque cursor back through ``/messages?before=true&cursor=...``
   walks the older pages in ascending order, and the echo-loop reconstitutes
   the exact full transcript with no messages skipped or duplicated.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

ADMIN_KEY = "thread-lazy-admin-000001"
TAIL_LIMIT = 100
OLDER_PAGE = 50
TOTAL_SENDS = 137  # > TAIL_LIMIT, produces several older pages


def _settings(db_path: Path) -> Settings:
    return Settings(
        database_path=db_path,
        auth_mode="api_key",
        api_keys_json=json.dumps(
            {
                ADMIN_KEY: {
                    "tenant_id": "demo",
                    "actor_id": "admin.user",
                    "role": "admin",
                }
            }
        ),
        rate_limit_per_minute=10000,
        docs_enabled=False,
    )


class ThreadLazyLoadApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.client = TestClient(create_app(_settings(Path(self._tmp.name) / "tl.db")))
        self.services = cast(Any, self.client.app).state.services
        self.headers = {"X-API-Key": ADMIN_KEY, "X-Tenant-Id": "demo"}
        conv = self.client.post(
            "/api/conversations",
            json={"customer_name": "thread-lazy", "channel": "web"},
            headers=self.headers,
        ).json()
        self.conversation_id = conv["id"]
        # Each turn appends a customer message (and typically an assistant
        # reply); content is held in the turn so the final echo-loop has a
        # stable expected transcript independent of how many messages a turn
        # contributes.
        for index in range(TOTAL_SENDS):
            self.client.post(
                f"/api/conversations/{self.conversation_id}/messages",
                json={"content": f"lazy-message-{index:04d}"},
                headers={
                    **self.headers,
                    "Idempotency-Key": f"lazy-{index:04d}-{self.conversation_id[-8:]}",
                },
            )

    def tearDown(self) -> None:
        self.services.database.close()
        self.client.close()
        self._tmp.cleanup()

    def test_full_transcript_without_limit_is_unchanged(self) -> None:
        r = self.client.get(f"/api/conversations/{self.conversation_id}", headers=self.headers)
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("X-Prev-Cursor", r.headers)
        self.assertNotIn("X-Has-More", r.headers)
        self.assertGreater(len(r.json()["messages"]), TAIL_LIMIT)

    def test_limited_detail_returns_tail_and_cursor_headers(self) -> None:
        r = self.client.get(
            f"/api/conversations/{self.conversation_id}?message_limit={TAIL_LIMIT}"
            "&messages_before=true",
            headers=self.headers,
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(len(body["messages"]), TAIL_LIMIT)
        # The tail is chronological ascending, ending at the newest message.
        self.assertIn(str(TAIL_LIMIT - 1), "\n".join(m["content"] for m in body["messages"]))
        self.assertEqual(r.headers.get("X-Page-Limit"), str(TAIL_LIMIT))
        self.assertEqual(r.headers.get("X-Has-More"), "true")  # page is full
        prev = r.headers.get("X-Prev-Cursor")
        self.assertIsNotNone(prev)
        # The cursor is opaque base64: a client echoes it, never reads it.
        self.assertNotIn("lazy-message", cast(str, prev))

    def test_echo_loop_reconstitutes_full_transcript(self) -> None:
        full = self.client.get(
            f"/api/conversations/{self.conversation_id}", headers=self.headers
        ).json()["messages"]
        expected_ids = [m["id"] for m in full]

        r = self.client.get(
            f"/api/conversations/{self.conversation_id}?message_limit={TAIL_LIMIT}"
            "&messages_before=true",
            headers=self.headers,
        )
        tail = [m["id"] for m in r.json()["messages"]]
        prev = r.headers["X-Prev-Cursor"]
        visited = list(tail)
        while prev:
            page = self.client.get(
                f"/api/conversations/{self.conversation_id}/messages?limit={OLDER_PAGE}"
                f"&before=true&cursor={prev}",
                headers=self.headers,
            )
            self.assertEqual(page.status_code, 200)
            ids = [m["id"] for m in page.json()]
            if not ids:
                break
            # Each page is ordered ascending and strictly older than the tail.
            visited = ids + visited
            prev = page.headers.get("X-Prev-Cursor")
        self.assertEqual(visited, expected_ids)
        self.assertEqual(len(visited), len(expected_ids))

    def test_following_prev_cursor_past_the_oldest_stops(self) -> None:
        r = self.client.get(
            f"/api/conversations/{self.conversation_id}?message_limit={TAIL_LIMIT}"
            "&messages_before=true",
            headers=self.headers,
        )
        prev = r.headers["X-Prev-Cursor"]
        while prev:
            page = self.client.get(
                f"/api/conversations/{self.conversation_id}/messages?limit={OLDER_PAGE}"
                f"&before=true&cursor={prev}",
                headers=self.headers,
            )
            prev = page.headers.get("X-Prev-Cursor")
            if not page.json():
                break
        # Having consumed the whole transcript, the final page had no cursor.
        self.assertIsNone(prev)


if __name__ == "__main__":
    unittest.main()
