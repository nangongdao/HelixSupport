"""Phase 20.3: HTTP connector reference implementation tests."""

from __future__ import annotations

import unittest
from typing import Any

from app.connectors_http import (
    HttpConnectorConfig,
    HttpConnectorError,
    HttpCRMConnector,
    HttpKnowledgeConnector,
    HttpOrderConnector,
    _hmac_sign,
)
from app.connectors_runtime import TransientConnectorError


class _FakeTransport:
    """Records calls and returns a canned (status, payload) response."""

    def __init__(self, status: int, payload: dict[str, Any] | None = None) -> None:
        self.status = status
        self.payload = payload or {}
        self.calls: list[tuple] = []

    def __call__(self, method, url, params, headers, timeout):
        self.calls.append((method, url, params, headers, timeout))
        return self.status, self.payload


_CONFIG = HttpConnectorConfig(
    base_url="https://orders.example.com",
    timeout_seconds=5.0,
    signature_secret="shared-secret",
)


class HttpOrderConnectorTests(unittest.TestCase):
    def test_lookup_ok(self) -> None:
        transport = _FakeTransport(
            200,
            {
                "order": {
                    "id": "ORD-1",
                    "status": "运输中",
                    "eta": "2026-07-20",
                    "tracking_code": "SF1",
                }
            },
        )
        conn = HttpOrderConnector(_CONFIG, transport=transport)
        result = conn.lookup_order("t1", "CUST-1", "ORD-1")
        self.assertTrue(result.ok)
        self.assertEqual(result.code, "ok")
        assert result.order is not None
        self.assertEqual(result.order.id, "ORD-1")
        self.assertEqual(result.order.status, "运输中")

    def test_lookup_not_found(self) -> None:
        conn = HttpOrderConnector(_CONFIG, transport=_FakeTransport(404))
        result = conn.lookup_order("t1", "CUST-1", "ORD-X")
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "not_found")

    def test_identity_required_without_call(self) -> None:
        transport = _FakeTransport(200)
        conn = HttpOrderConnector(_CONFIG, transport=transport)
        result = conn.lookup_order("t1", None, "ORD-1")
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "identity_required")
        self.assertEqual(transport.calls, [])  # no HTTP call made

    def test_transient_5xx_raises_retryable(self) -> None:
        conn = HttpOrderConnector(_CONFIG, transport=_FakeTransport(503))
        with self.assertRaises(TransientConnectorError):
            conn.lookup_order("t1", "CUST-1", "ORD-1")

    def test_transient_429_raises_retryable(self) -> None:
        conn = HttpOrderConnector(_CONFIG, transport=_FakeTransport(429))
        with self.assertRaises(TransientConnectorError):
            conn.lookup_order("t1", "CUST-1", "ORD-1")

    def test_non_transient_4xx_raises_connector_error(self) -> None:
        conn = HttpOrderConnector(_CONFIG, transport=_FakeTransport(400))
        with self.assertRaises(HttpConnectorError):
            conn.lookup_order("t1", "CUST-1", "ORD-1")

    def test_request_signed_with_hmac(self) -> None:
        transport = _FakeTransport(200, {"order": {"id": "O", "status": "s"}})
        conn = HttpOrderConnector(_CONFIG, transport=transport)
        conn.lookup_order("t1", "CUST-1", "ORD-1")
        self.assertEqual(len(transport.calls), 1)
        _, _, _, headers, _ = transport.calls[0]
        self.assertIn("X-Helix-Signature", headers)
        self.assertIn("X-Helix-Timestamp", headers)
        # The signature covers method, path, sorted query, and timestamp.
        expected = _hmac_sign(
            "shared-secret",
            "GET",
            "/orders/ORD-1",
            headers["X-Helix-Timestamp"],
            params={"customer_ref": "CUST-1", "tenant_id": "t1"},
        )
        self.assertEqual(headers["X-Helix-Signature"], expected)
        self.assertNotEqual(
            headers["X-Helix-Signature"],
            _hmac_sign("shared-secret", "GET", "/orders/ORD-1", headers["X-Helix-Timestamp"]),
        )


class HttpCRMConnectorTests(unittest.TestCase):
    def test_resolve_ok(self) -> None:
        transport = _FakeTransport(200, {"customer_ref": "CUST-1", "name": "林嘉"})
        conn = HttpCRMConnector(_CONFIG, transport=transport)
        result = conn.resolve_customer("t1", "CUST-1")
        self.assertTrue(result.ok)
        self.assertEqual(result.code, "ok")
        assert result.profile is not None
        self.assertEqual(result.profile.customer_ref, "CUST-1")
        self.assertEqual(result.profile.name, "林嘉")

    def test_resolve_not_found(self) -> None:
        result = HttpCRMConnector(_CONFIG, transport=_FakeTransport(404)).resolve_customer(
            "t1", "CUST-9"
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "not_found")
        self.assertIsNone(result.profile)

    def test_resolve_transient(self) -> None:
        conn = HttpCRMConnector(_CONFIG, transport=_FakeTransport(502))
        with self.assertRaises(TransientConnectorError):
            conn.resolve_customer("t1", "CUST-1")

    def test_resolve_non_transient(self) -> None:
        conn = HttpCRMConnector(_CONFIG, transport=_FakeTransport(403))
        with self.assertRaises(HttpConnectorError):
            conn.resolve_customer("t1", "CUST-1")


class HttpKnowledgeConnectorTests(unittest.TestCase):
    def test_search_ok(self) -> None:
        transport = _FakeTransport(
            200,
            {
                "hits": [
                    {
                        "article_id": "kb-shipping",
                        "title": "配送时效",
                        "content": "2-4 个工作日",
                        "score": 8,
                    }
                ]
            },
        )
        hits = HttpKnowledgeConnector(_CONFIG, transport=transport).search("t1", "配送")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].article_id, "kb-shipping")
        self.assertEqual(hits[0].title, "配送时效")

    def test_search_transient(self) -> None:
        conn = HttpKnowledgeConnector(_CONFIG, transport=_FakeTransport(503))
        with self.assertRaises(TransientConnectorError):
            conn.search("t1", "配送")

    def test_search_non_transient(self) -> None:
        conn = HttpKnowledgeConnector(_CONFIG, transport=_FakeTransport(400))
        with self.assertRaises(HttpConnectorError):
            conn.search("t1", "配送")


class HttpConnectorConfigTests(unittest.TestCase):
    def test_rejects_empty_base_url(self) -> None:
        with self.assertRaises(ValueError):
            HttpConnectorConfig(base_url="")

    def test_rejects_non_positive_timeout(self) -> None:
        with self.assertRaises(ValueError):
            HttpConnectorConfig(base_url="https://x", timeout_seconds=0)


if __name__ == "__main__":
    unittest.main()
