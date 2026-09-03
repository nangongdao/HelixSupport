"""Phase 42.6: unified trace context — request → job → audit.

One correlation id must survive the async boundary: the HTTP request that
enqueued a turn job stamps the job row, and when a worker claims the job it
re-applies that id so every audit event emitted while processing carries the
same correlation id. On-call can go from a page to the job to its audit
trail with a single value.
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


class TraceContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        settings = Settings(
            database_path=Path(self._tmp.name) / "trace.db",
            turn_worker_enabled=False,
            rate_limit_per_minute=10_000,
            auth_mode="api_key",
            api_keys_json=json.dumps(
                {
                    "admin-key-0123456789": {
                        "tenant_id": "demo",
                        "actor_id": "admin",
                        "role": "admin",
                    }
                }
            ),
            # The channel webhook ingress always enqueues an async turn job,
            # which is exactly the boundary the trace context must survive.
            channel_webhooks_json=json.dumps(
                {
                    "trace-account": {
                        "tenant_id": "demo",
                        "channel": "web",
                        "secret": "trace-channel-secret-0123456789abcdef",
                    }
                }
            ),
        )
        self.client = TestClient(create_app(settings))
        self.services = cast(Any, self.client.app).state.services
        self.headers = {"X-API-Key": "admin-key-0123456789", "X-Tenant-Id": "demo"}

    def tearDown(self) -> None:
        self.client.close()
        self.services.database.close()
        self._tmp.cleanup()

    def _deliver_channel_message(self, request_id: str, message_id: str, content: str) -> Any:
        import hashlib
        import hmac
        import time

        body = json.dumps(
            {
                "message_id": message_id,
                "thread_id": "trace-thread-1",
                "customer_id": "CUST-TRACE",
                "customer_name": "Trace Customer",
                "content": content,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        timestamp = int(time.time())
        secret = b"trace-channel-secret-0123456789abcdef"
        signature = hmac.new(secret, f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
        return self.client.post(
            "/api/channels/trace-account/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Request-Id": request_id,
                "X-Helix-Timestamp": str(timestamp),
                "X-Helix-Signature": f"sha256={signature}",
            },
        )

    def test_request_id_stamps_job_and_survives_the_worker(self) -> None:
        request_id = "req_trace-42-check"
        delivered = self._deliver_channel_message(request_id, "trace-msg-1", "correlate me")
        self.assertEqual(delivered.status_code, 202, delivered.text)
        conversation_id = delivered.json()["conversation_id"]

        # The job row carries the originating request id.
        with self.services.database.connect() as connection:
            jobs = connection.execute(
                "SELECT id, request_id FROM turn_jobs WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchall()
        self.assertTrue(jobs, "a turn job must have been enqueued")
        for row in jobs:
            self.assertEqual(row["request_id"], request_id)

        # The worker re-applies the context: audits emitted while processing
        # carry the same correlation id.
        while self.services.turn_worker.run_once("trace-test-worker"):
            pass
        events = [
            event
            for event in self.db_request_ids(conversation_id)
            if event["request_id"] is not None
        ]
        self.assertTrue(events, "processing must emit audited events")
        for event in events:
            self.assertEqual(event["request_id"], request_id)

    def db_request_ids(self, conversation_id: str) -> list[dict[str, Any]]:
        with self.services.database.connect() as connection:
            rows = connection.execute(
                "SELECT event_type, request_id FROM audit_events "
                "WHERE tenant_id = 'demo' AND conversation_id = ?",
                (conversation_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def test_unsupplied_requests_get_a_generated_id_not_none(self) -> None:
        created = self.client.post(
            "/api/conversations",
            json={"customer_name": "No Header", "channel": "web"},
            headers=self.headers,
        )
        self.assertEqual(created.status_code, 201)
        request_id = created.headers.get("X-Request-Id", "")
        self.assertTrue(request_id.startswith("req_"))


if __name__ == "__main__":
    unittest.main()
