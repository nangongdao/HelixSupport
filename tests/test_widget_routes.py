"""Widget API routes integration tests (Phase 23)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.widget_token import sign_token


class WidgetRoutesTests(unittest.TestCase):
    """Test widget API endpoints with signed token authentication."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "widget-routes.db"
        self.widget_secret = "test-widget-secret-key"
        self.settings = Settings(
            database_path=self.db_path,
            auth_mode="demo",
            api_keys_json=json.dumps({}),
            docs_enabled=False,
            widget_secret=self.widget_secret,
            widget_frame_ancestors=("'self'",),
        )
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.tenant_id = "tenant-widget-test"
        # Provision tenant directly via database
        from app.database import Database

        self.db = Database(self.db_path)
        self.db.initialize()
        self.db.ensure_tenant(self.tenant_id, "Widget Test Tenant")

    def tearDown(self) -> None:
        self.client.close()
        self.db.close()
        # Windows: wait for connections to release before cleanup
        import gc

        gc.collect()
        try:
            self.tmp.cleanup()
        except (PermissionError, OSError):
            # Windows file lock - ignore cleanup error in tests
            pass

    def _sign_token(
        self,
        tenant_id: str | None = None,
        customer_ref: str | None = None,
        conversation_id: str | None = None,
        ttl: int = 3600,
    ) -> str:
        """Helper to sign a widget token."""
        return sign_token(
            secret=self.widget_secret,
            tenant_id=tenant_id or self.tenant_id,
            customer_ref=customer_ref,
            conversation_id=conversation_id,
            ttl_seconds=ttl,
        )

    def test_create_session_success(self) -> None:
        """POST /api/widget/sessions creates conversation and returns session token."""
        token = self._sign_token()
        response = self.client.post(
            "/api/widget/sessions",
            json={"customer_name": "Alice", "channel": "web_chat"},
            headers={"X-Widget-Token": token},
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertIn("conversation", data)
        self.assertIn("widget_token", data)
        self.assertEqual(data["conversation"]["customer_name"], "Alice")
        self.assertIsNotNone(data["widget_token"])

    def test_create_session_missing_token(self) -> None:
        """POST /api/widget/sessions without X-Widget-Token returns 401."""
        response = self.client.post(
            "/api/widget/sessions",
            json={"customer_name": "Bob", "channel": "web_chat"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertIn("Missing X-Widget-Token", response.json()["detail"])

    def test_create_session_invalid_token(self) -> None:
        """POST /api/widget/sessions with invalid token returns 401."""
        response = self.client.post(
            "/api/widget/sessions",
            json={"customer_name": "Charlie", "channel": "web_chat"},
            headers={"X-Widget-Token": "invalid.token.here"},
        )
        self.assertEqual(response.status_code, 401)

    def test_create_session_nonexistent_tenant(self) -> None:
        """POST /api/widget/sessions for nonexistent tenant returns 404."""
        token = self._sign_token(tenant_id="nonexistent-tenant")
        response = self.client.post(
            "/api/widget/sessions",
            json={"customer_name": "Dave", "channel": "web_chat"},
            headers={"X-Widget-Token": token},
        )
        self.assertEqual(response.status_code, 404)
        self.assertIn("Tenant not found", response.json()["detail"])

    def test_create_session_default_customer_name(self) -> None:
        """POST /api/widget/sessions without customer_name uses 'Widget Visitor'."""
        token = self._sign_token()
        response = self.client.post(
            "/api/widget/sessions",
            json={"channel": "web_chat"},
            headers={"X-Widget-Token": token},
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["conversation"]["customer_name"], "Widget Visitor")

    def test_history_exposes_csat_survey_url_when_resolved(self) -> None:
        """ROADMAP 2.10.0: when the conversation is resolved and a CSAT
        survey is still pending, the history endpoint hands the widget its
        rating link via the X-CSAT-Survey-URL header — the customer side of
        the CSAT loop was previously unreachable in the widget channel."""
        init_token = self._sign_token()
        session_resp = self.client.post(
            "/api/widget/sessions",
            json={"customer_name": "CSAT Customer"},
            headers={"X-Widget-Token": init_token},
        )
        session_data = session_resp.json()
        conversation_id = session_data["conversation"]["id"]
        session_token = session_data["widget_token"]
        widget_headers = {"X-Widget-Token": session_token}

        # Unresolved: no survey header.
        unresolved = self.client.get(
            f"/api/widget/sessions/{conversation_id}/messages?limit=50",
            headers=widget_headers,
        )
        self.assertEqual(unresolved.status_code, 200)
        self.assertNotIn("X-CSAT-Survey-URL", unresolved.headers)

        # Resolve (creates the one-time survey) -> the header appears.
        self.db.transition_conversation(
            self.tenant_id,
            conversation_id,
            ["open"],
            "resolved",
        )
        expires = "2027-01-01T00:00:00+00:00"
        survey_token = self.db.create_csat_survey(self.tenant_id, conversation_id, expires)
        resolved = self.client.get(
            f"/api/widget/sessions/{conversation_id}/messages?limit=50",
            headers=widget_headers,
        )
        self.assertEqual(resolved.status_code, 200)
        survey_url = resolved.headers.get("X-CSAT-Survey-URL")
        self.assertIsNotNone(survey_url)
        self.assertIn(survey_token, survey_url)

    def test_history_header_absent_after_survey_responded(self) -> None:
        init_token = self._sign_token()
        session_resp = self.client.post(
            "/api/widget/sessions",
            json={"customer_name": "CSAT Customer 2"},
            headers={"X-Widget-Token": init_token},
        )
        session_data = session_resp.json()
        conversation_id = session_data["conversation"]["id"]
        session_token = session_data["widget_token"]
        widget_headers = {"X-Widget-Token": session_token}
        self.db.transition_conversation(self.tenant_id, conversation_id, ["open"], "resolved")
        survey_token = self.db.create_csat_survey(
            self.tenant_id, conversation_id, "2027-01-01T00:00:00+00:00"
        )
        self.db.submit_csat_rating(survey_token, 1)

        responded = self.client.get(
            f"/api/widget/sessions/{conversation_id}/messages?limit=50",
            headers=widget_headers,
        )
        self.assertEqual(responded.status_code, 200)
        # The survey was answered: no second rating link.
        self.assertNotIn("X-CSAT-Survey-URL", responded.headers)

    def test_send_message_sync_success(self) -> None:
        """POST /api/widget/sessions/{id}/messages (sync) returns TurnResponse."""
        # Create session first
        init_token = self._sign_token()
        session_resp = self.client.post(
            "/api/widget/sessions",
            json={"customer_name": "Eve", "channel": "web_chat"},
            headers={"X-Widget-Token": init_token},
        )
        session_data = session_resp.json()
        conversation_id = session_data["conversation"]["id"]
        session_token = session_data["widget_token"]

        # Send message
        response = self.client.post(
            f"/api/widget/sessions/{conversation_id}/messages",
            json={"content": "Hello", "channel_message_id": "msg-001"},
            headers={"X-Widget-Token": session_token},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("customer_message", data)
        self.assertIn("assistant_message", data)
        self.assertIn("conversation", data)
        self.assertEqual(data["customer_message"]["content"], "Hello")

    def test_send_message_wrong_conversation_token(self) -> None:
        """POST /messages with token for different conversation returns 404."""
        # Create session
        init_token = self._sign_token()
        session_resp = self.client.post(
            "/api/widget/sessions",
            json={"customer_name": "Frank", "channel": "web_chat"},
            headers={"X-Widget-Token": init_token},
        )
        conversation_id = session_resp.json()["conversation"]["id"]

        # Use token for different conversation
        wrong_token = self._sign_token(conversation_id="other-conv-id")
        response = self.client.post(
            f"/api/widget/sessions/{conversation_id}/messages",
            json={"content": "Test"},
            headers={"X-Widget-Token": wrong_token},
        )
        self.assertEqual(response.status_code, 404)
        self.assertIn("Conversation not found", response.json()["detail"])

    def test_send_message_idempotent_replay(self) -> None:
        """Replaying same channel_message_id returns cached turn."""
        # Create session
        init_token = self._sign_token()
        session_resp = self.client.post(
            "/api/widget/sessions",
            json={"customer_name": "Grace", "channel": "web_chat"},
            headers={"X-Widget-Token": init_token},
        )
        session_data = session_resp.json()
        conversation_id = session_data["conversation"]["id"]
        session_token = session_data["widget_token"]

        # Send message first time
        response1 = self.client.post(
            f"/api/widget/sessions/{conversation_id}/messages",
            json={"content": "First", "channel_message_id": "msg-replay-001"},
            headers={"X-Widget-Token": session_token},
        )
        data1 = response1.json()
        msg_id_1 = data1["customer_message"]["id"]

        # Replay same message
        response2 = self.client.post(
            f"/api/widget/sessions/{conversation_id}/messages",
            json={"content": "First", "channel_message_id": "msg-replay-001"},
            headers={"X-Widget-Token": session_token},
        )
        data2 = response2.json()
        self.assertEqual(data2["customer_message"]["id"], msg_id_1)
        self.assertTrue(data2.get("idempotent_replay"))

    def test_send_message_async_mode(self) -> None:
        """POST /messages?async_mode=true enqueues job and returns job_id."""
        # Create session
        init_token = self._sign_token()
        session_resp = self.client.post(
            "/api/widget/sessions",
            json={"customer_name": "Henry", "channel": "web_chat"},
            headers={"X-Widget-Token": init_token},
        )
        session_data = session_resp.json()
        conversation_id = session_data["conversation"]["id"]
        session_token = session_data["widget_token"]

        # Send async
        response = self.client.post(
            f"/api/widget/sessions/{conversation_id}/messages?async_mode=true",
            json={"content": "Async test", "channel_message_id": "msg-async-001"},
            headers={"X-Widget-Token": session_token},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("job_id", data)
        self.assertIn("status", data)

    def test_list_messages_success(self) -> None:
        """GET /api/widget/sessions/{id}/messages returns message list."""
        # Create session and send message
        init_token = self._sign_token()
        session_resp = self.client.post(
            "/api/widget/sessions",
            json={"customer_name": "Ivy", "channel": "web_chat"},
            headers={"X-Widget-Token": init_token},
        )
        session_data = session_resp.json()
        conversation_id = session_data["conversation"]["id"]
        session_token = session_data["widget_token"]

        self.client.post(
            f"/api/widget/sessions/{conversation_id}/messages",
            json={"content": "Test message"},
            headers={"X-Widget-Token": session_token},
        )

        # List messages
        response = self.client.get(
            f"/api/widget/sessions/{conversation_id}/messages",
            headers={"X-Widget-Token": session_token},
        )
        self.assertEqual(response.status_code, 200)
        messages = response.json()
        self.assertIsInstance(messages, list)
        self.assertGreater(len(messages), 0)
        self.assertIn("X-Conversation-Status", response.headers)

    def test_list_messages_limit_parameter(self) -> None:
        """GET /messages?limit=N respects limit parameter."""
        # Create session
        init_token = self._sign_token()
        session_resp = self.client.post(
            "/api/widget/sessions",
            json={"customer_name": "Jack", "channel": "web_chat"},
            headers={"X-Widget-Token": init_token},
        )
        session_data = session_resp.json()
        conversation_id = session_data["conversation"]["id"]
        session_token = session_data["widget_token"]

        # List with limit
        response = self.client.get(
            f"/api/widget/sessions/{conversation_id}/messages?limit=10",
            headers={"X-Widget-Token": session_token},
        )
        self.assertEqual(response.status_code, 200)

    def test_stream_turn_no_job(self) -> None:
        """GET /stream for conversation with no turn job returns 404."""
        # Create session without sending message
        init_token = self._sign_token()
        session_resp = self.client.post(
            "/api/widget/sessions",
            json={"customer_name": "Kate", "channel": "web_chat"},
            headers={"X-Widget-Token": init_token},
        )
        session_data = session_resp.json()
        conversation_id = session_data["conversation"]["id"]
        session_token = session_data["widget_token"]

        response = self.client.get(
            f"/api/widget/sessions/{conversation_id}/stream",
            headers={"X-Widget-Token": session_token},
        )
        self.assertEqual(response.status_code, 404)
        self.assertIn("No turn job", response.json()["detail"])

    def test_stream_turn_timeout_parameter(self) -> None:
        """GET /stream?timeout=N uses custom timeout."""
        # Create session and send async message to create job
        init_token = self._sign_token()
        session_resp = self.client.post(
            "/api/widget/sessions",
            json={"customer_name": "Leo", "channel": "web_chat"},
            headers={"X-Widget-Token": init_token},
        )
        session_data = session_resp.json()
        conversation_id = session_data["conversation"]["id"]
        session_token = session_data["widget_token"]

        self.client.post(
            f"/api/widget/sessions/{conversation_id}/messages?async_mode=true",
            json={"content": "Stream test"},
            headers={"X-Widget-Token": session_token},
        )

        # Stream with custom timeout (should start successfully)
        response = self.client.get(
            f"/api/widget/sessions/{conversation_id}/stream?timeout=5",
            headers={"X-Widget-Token": session_token},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/event-stream", response.headers["content-type"])

    def test_create_session_without_channel_message_id(self) -> None:
        """POST /messages without channel_message_id still works."""
        init_token = self._sign_token()
        session_resp = self.client.post(
            "/api/widget/sessions",
            json={"customer_name": "Mike", "channel": "web_chat"},
            headers={"X-Widget-Token": init_token},
        )
        session_data = session_resp.json()
        conversation_id = session_data["conversation"]["id"]
        session_token = session_data["widget_token"]

        # Send without channel_message_id
        response = self.client.post(
            f"/api/widget/sessions/{conversation_id}/messages",
            json={"content": "No channel ID"},
            headers={"X-Widget-Token": session_token},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("customer_message", data)

    def test_send_message_to_nonexistent_conversation(self) -> None:
        """POST /messages to non-existent conversation returns 404."""
        fake_conv_id = "conv_nonexistent123"
        session_token = self._sign_token(conversation_id=fake_conv_id)

        response = self.client.post(
            f"/api/widget/sessions/{fake_conv_id}/messages",
            json={"content": "Test"},
            headers={"X-Widget-Token": session_token},
        )
        self.assertEqual(response.status_code, 404)

    def test_list_messages_nonexistent_conversation(self) -> None:
        """GET /messages for non-existent conversation returns 404."""
        fake_conv_id = "conv_fake999"
        session_token = self._sign_token(conversation_id=fake_conv_id)

        response = self.client.get(
            f"/api/widget/sessions/{fake_conv_id}/messages",
            headers={"X-Widget-Token": session_token},
        )
        self.assertEqual(response.status_code, 404)

    def test_send_message_orchestrator_turn_in_progress_error(self) -> None:
        """POST /messages with turn in progress returns 409."""
        init_token = self._sign_token()
        session_resp = self.client.post(
            "/api/widget/sessions",
            json={"customer_name": "Nina", "channel": "web_chat"},
            headers={"X-Widget-Token": init_token},
        )
        session_data = session_resp.json()
        conversation_id = session_data["conversation"]["id"]
        session_token = session_data["widget_token"]

        # Mock orchestrator to raise TurnInProgressError
        from app.orchestrator import TurnInProgressError

        with patch.object(
            self.app.state.services.orchestrator, "handle_customer_message"
        ) as mock_handle:
            mock_handle.side_effect = TurnInProgressError("Turn already in progress")
            response = self.client.post(
                f"/api/widget/sessions/{conversation_id}/messages",
                json={"content": "Test conflict"},
                headers={"X-Widget-Token": session_token},
            )
            self.assertEqual(response.status_code, 409)
            self.assertIn("Turn already in progress", response.json()["detail"])

    def test_send_message_orchestrator_value_error(self) -> None:
        """POST /messages with invalid input returns 422."""
        init_token = self._sign_token()
        session_resp = self.client.post(
            "/api/widget/sessions",
            json={"customer_name": "Oscar", "channel": "web_chat"},
            headers={"X-Widget-Token": init_token},
        )
        session_data = session_resp.json()
        conversation_id = session_data["conversation"]["id"]
        session_token = session_data["widget_token"]

        # Mock orchestrator to raise ValueError
        with patch.object(
            self.app.state.services.orchestrator, "handle_customer_message"
        ) as mock_handle:
            mock_handle.side_effect = ValueError("Invalid message content")
            response = self.client.post(
                f"/api/widget/sessions/{conversation_id}/messages",
                json={"content": "Bad input"},
                headers={"X-Widget-Token": session_token},
            )
            self.assertEqual(response.status_code, 422)
            self.assertIn("Invalid message content", response.json()["detail"])

    def test_send_message_orchestrator_lookup_error(self) -> None:
        """POST /messages with lookup failure returns 404."""
        init_token = self._sign_token()
        session_resp = self.client.post(
            "/api/widget/sessions",
            json={"customer_name": "Paula", "channel": "web_chat"},
            headers={"X-Widget-Token": init_token},
        )
        session_data = session_resp.json()
        conversation_id = session_data["conversation"]["id"]
        session_token = session_data["widget_token"]

        # Mock orchestrator to raise LookupError
        with patch.object(
            self.app.state.services.orchestrator, "handle_customer_message"
        ) as mock_handle:
            mock_handle.side_effect = LookupError("Resource not found")
            response = self.client.post(
                f"/api/widget/sessions/{conversation_id}/messages",
                json={"content": "Lookup test"},
                headers={"X-Widget-Token": session_token},
            )
            self.assertEqual(response.status_code, 404)
            self.assertIn("Resource not found", response.json()["detail"])

    def test_send_message_idempotency_conflict_error(self) -> None:
        """POST /messages with idempotency conflict returns 409."""
        init_token = self._sign_token()
        session_resp = self.client.post(
            "/api/widget/sessions",
            json={"customer_name": "Quinn", "channel": "web_chat"},
            headers={"X-Widget-Token": init_token},
        )
        session_data = session_resp.json()
        conversation_id = session_data["conversation"]["id"]
        session_token = session_data["widget_token"]

        # Mock orchestrator to raise IdempotencyConflictError
        from app.orchestrator import IdempotencyConflictError

        with patch.object(
            self.app.state.services.orchestrator, "handle_customer_message"
        ) as mock_handle:
            mock_handle.side_effect = IdempotencyConflictError("Idempotency key mismatch")
            response = self.client.post(
                f"/api/widget/sessions/{conversation_id}/messages",
                json={"content": "Conflict test"},
                headers={"X-Widget-Token": session_token},
            )
            self.assertEqual(response.status_code, 409)
            self.assertIn("Idempotency key mismatch", response.json()["detail"])

    def test_send_message_invalid_transition_error(self) -> None:
        """POST /messages with invalid state transition returns 409."""
        init_token = self._sign_token()
        session_resp = self.client.post(
            "/api/widget/sessions",
            json={"customer_name": "Rachel", "channel": "web_chat"},
            headers={"X-Widget-Token": init_token},
        )
        session_data = session_resp.json()
        conversation_id = session_data["conversation"]["id"]
        session_token = session_data["widget_token"]

        # Mock orchestrator to raise InvalidTransitionError
        from app.orchestrator import InvalidTransitionError

        with patch.object(
            self.app.state.services.orchestrator, "handle_customer_message"
        ) as mock_handle:
            mock_handle.side_effect = InvalidTransitionError("Cannot transition from resolved")
            response = self.client.post(
                f"/api/widget/sessions/{conversation_id}/messages",
                json={"content": "Transition test"},
                headers={"X-Widget-Token": session_token},
            )
            self.assertEqual(response.status_code, 409)
            self.assertIn("Cannot transition from resolved", response.json()["detail"])

    def test_send_message_async_with_backpressure(self) -> None:
        """POST /messages?async_mode=true with backpressure returns 429."""
        init_token = self._sign_token()
        session_resp = self.client.post(
            "/api/widget/sessions",
            json={"customer_name": "Sam", "channel": "web_chat"},
            headers={"X-Widget-Token": init_token},
        )
        session_data = session_resp.json()
        conversation_id = session_data["conversation"]["id"]
        session_token = session_data["widget_token"]

        # Mock backpressure check to return overload reason
        with patch(
            "app.widget_routes.backpressure_reason", return_value="Queue full, try again later"
        ):
            response = self.client.post(
                f"/api/widget/sessions/{conversation_id}/messages?async_mode=true",
                json={"content": "Test backpressure"},
                headers={"X-Widget-Token": session_token},
            )
            self.assertEqual(response.status_code, 429)
            self.assertIn("Queue full", response.json()["detail"])
            self.assertEqual(response.headers.get("Retry-After"), "30")

    def test_send_message_unhandled_exception(self) -> None:
        """POST /messages with unhandled exception re-raises it."""
        init_token = self._sign_token()
        session_resp = self.client.post(
            "/api/widget/sessions",
            json={"customer_name": "Tina", "channel": "web_chat"},
            headers={"X-Widget-Token": init_token},
        )
        session_data = session_resp.json()
        conversation_id = session_data["conversation"]["id"]
        session_token = session_data["widget_token"]

        # Mock orchestrator to raise an unexpected exception type
        # Use a new client with raise_server_exceptions=False to capture 500 errors
        test_client = TestClient(self.app, raise_server_exceptions=False)
        with patch.object(
            self.app.state.services.orchestrator, "handle_customer_message"
        ) as mock_handle:
            mock_handle.side_effect = RuntimeError("Unexpected internal error")
            response = test_client.post(
                f"/api/widget/sessions/{conversation_id}/messages",
                json={"content": "Unhandled test"},
                headers={"X-Widget-Token": session_token},
            )
            # FastAPI wraps unhandled exceptions as 500
            self.assertEqual(response.status_code, 500)
        test_client.close()


if __name__ == "__main__":
    unittest.main()
