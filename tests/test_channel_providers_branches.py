"""Provider adapter SDK (ROADMAP 42.5 / REL-003) — branch coverage.

Complements tests/test_provider_conformance.py (full HTTP journey) with the
pure adapter branches: signature header validation (missing / malformed /
out-of-window / bad HMAC), payload normalization errors (JSON / shape /
kind / id length / attachments), and adapter lookup.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import unittest

from app.channel_providers import (
    EVENT_KINDS,
    PROVIDER_ADAPTERS,
    ReferenceJsonAdapter,
    get_provider_adapter,
    sign_reference_request,
    verify_reference_signature,
)
from app.channel_webhooks import ChannelWebhookAuthError


def _signed_headers(
    secret: bytes, body: bytes, *, now_epoch: int | None = None, scheme: str = "sha256="
) -> dict[str, str]:
    timestamp = now_epoch if now_epoch is not None else int(time.time())
    signature = hmac.new(secret, f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return {
        "X-Helix-Timestamp": str(timestamp),
        "X-Helix-Signature": f"{scheme}{signature}",
    }


class VerifyReferenceSignatureTests(unittest.TestCase):
    SECRET = b"provider-secret"

    def test_valid_signature_passes(self) -> None:
        body = b'{"message_id": "m1"}'
        now = int(time.time())
        verify_reference_signature(
            self.SECRET, _signed_headers(self.SECRET, body, now_epoch=now), body, now_epoch=now
        )

    def test_missing_headers_rejected(self) -> None:
        with self.assertRaisesRegex(ChannelWebhookAuthError, "missing signature headers"):
            verify_reference_signature(self.SECRET, {}, b"body")

    def test_malformed_timestamp_rejected(self) -> None:
        headers = _signed_headers(self.SECRET, b"body")
        headers["X-Helix-Timestamp"] = "not-a-number"
        with self.assertRaisesRegex(ChannelWebhookAuthError, "malformed signature timestamp"):
            verify_reference_signature(self.SECRET, headers, b"body")

    def test_out_of_replay_window_rejected(self) -> None:
        now = int(time.time())
        headers = _signed_headers(self.SECRET, b"body", now_epoch=now - 400)
        with self.assertRaisesRegex(ChannelWebhookAuthError, "replay window"):
            verify_reference_signature(self.SECRET, headers, b"body", now_epoch=now)

    def test_wrong_signature_rejected(self) -> None:
        now = int(time.time())
        headers = _signed_headers(self.SECRET, b"body", now_epoch=now)
        headers["X-Helix-Signature"] = "sha256=deadbeef"
        with self.assertRaisesRegex(ChannelWebhookAuthError, "invalid signature"):
            verify_reference_signature(self.SECRET, headers, b"body", now_epoch=now)

    def test_sha256_prefix_is_optional(self) -> None:
        body = b'{"message_id": "m1"}'
        now = int(time.time())
        bare = _signed_headers(self.SECRET, body, now_epoch=now)
        bare["X-Helix-Signature"] = bare["X-Helix-Signature"].split("=", 1)[1]
        verify_reference_signature(self.SECRET, bare, body, now_epoch=now)


class ReferenceJsonAdapterTests(unittest.TestCase):
    SECRET = b"provider-secret"

    def setUp(self) -> None:
        self.adapter = ReferenceJsonAdapter()

    def _event_body(self, **overrides: object) -> bytes:
        payload = {
            "message_id": "m1",
            "thread_id": "t1",
            "customer_id": "c1",
            "customer_name": "Alice",
            "content": "hello",
            "kind": "message",
        }
        payload.update(overrides)
        return json.dumps(payload).encode("utf-8")

    def test_valid_message_parses(self) -> None:
        event = self.adapter.parse(self._event_body())
        self.assertEqual(event.kind, "message")
        self.assertEqual(event.external_message_id, "m1")
        self.assertEqual(event.customer_name, "Alice")
        self.assertEqual(event.attachments, ())

    def test_all_kinds_parse(self) -> None:
        for kind in EVENT_KINDS:
            event = self.adapter.parse(self._event_body(kind=kind))
            self.assertEqual(event.kind, kind)

    def test_edit_recall_receipt_kinds_parse(self) -> None:
        for kind in ("edit", "recall", "receipt"):
            event = self.adapter.parse(self._event_body(kind=kind))
            self.assertEqual(event.kind, kind)

    def test_invalid_json_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "not valid JSON"):
            self.adapter.parse(b"{not json")

    def test_non_object_payload_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "JSON object"):
            self.adapter.parse(b'["not", "object"]')

    def test_unknown_kind_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown provider event kind"):
            self.adapter.parse(self._event_body(kind="delete"))

    def test_missing_message_id_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "message_id"):
            self.adapter.parse(self._event_body(message_id=""))

    def test_missing_thread_id_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "thread_id"):
            self.adapter.parse(self._event_body(thread_id=""))

    def test_missing_customer_id_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "customer_id"):
            self.adapter.parse(self._event_body(customer_id=""))

    def test_attachment_refs_normalized(self) -> None:
        body = self._event_body(
            attachments=[
                {
                    "id": "a1",
                    "filename": "f.pdf",
                    "content_type": "application/pdf",
                    "url": "https://x/f.pdf",
                }
            ]
        )
        event = self.adapter.parse(body)
        self.assertEqual(len(event.attachments), 1)
        self.assertEqual(event.attachments[0].external_id, "a1")

    def test_non_object_attachment_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "attachment references must be objects"):
            self.adapter.parse(self._event_body(attachments=["not-object"]))

    def test_occurred_at_optional(self) -> None:
        event = self.adapter.parse(self._event_body())
        self.assertIsNone(event.occurred_at)
        event = self.adapter.parse(self._event_body(occurred_at="2026-09-01T00:00:00Z"))
        self.assertEqual(event.occurred_at, "2026-09-01T00:00:00Z")

    def test_verify_signature_delegates(self) -> None:
        body = self._event_body()
        now = int(time.time())
        headers = _signed_headers(self.SECRET, body, now_epoch=now)
        self.adapter.verify_signature(self.SECRET, headers, body)


class ProviderAdapterLookupTests(unittest.TestCase):
    def test_registry_has_reference(self) -> None:
        self.assertIn("reference", PROVIDER_ADAPTERS)
        self.assertEqual(get_provider_adapter("reference").name, "reference")

    def test_unknown_adapter_raises_value_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown provider adapter"):
            get_provider_adapter("nonexistent")

    def test_sign_reference_request_returns_headers(self) -> None:
        now = int(time.time())
        headers = sign_reference_request(b"secret", b"body", now_epoch=now)
        self.assertEqual(headers["X-Helix-Timestamp"], str(now))
        self.assertTrue(headers["X-Helix-Signature"].startswith("sha256="))


if __name__ == "__main__":
    unittest.main()
