"""Connector contract tests (Phase 20.4: parameterized conformance suite).

The ``OrderConnectorConformanceMixin`` runs any ``OrderConnector``
implementation through the behavioural rules orchestration depends on --
identity binding, cross-customer non-disclosure, and unknown-order handling.
A real connector (HTTP, gRPC, ...) is conformance-checked by subclassing the
mixin and supplying ``make_order_connector`` plus the known (customer, order)
pair its backing store serves. The sandbox and the Phase 20.3 HTTP reference
connector both pass the same suite here.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    _Assertions = unittest.TestCase
else:
    _Assertions = object

from app.connectors import (
    CRMConnector,
    KnowledgeConnector,
    OrderConnector,
    SandboxCRMConnector,
    SandboxKnowledgeConnector,
    SandboxOrderConnector,
)
from app.connectors_http import HttpConnectorConfig, HttpKnowledgeConnector, HttpOrderConnector
from app.database import Database


class _SandboxFixture:
    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._tmp.name) / "connectors.db")
        self.db.initialize()  # seeds the demo orders and knowledge articles

    def close(self) -> None:
        self.db.close()
        self._tmp.cleanup()


class OrderConnectorConformanceMixin(_Assertions):
    """Conformance suite any OrderConnector must pass (Phase 20.4).

    Subclasses implement ``make_order_connector`` returning
    ``(connector, known_customer_ref, known_order_id, known_order_status)``.
    The mixin's tests then verify identity binding, non-disclosure, and
    unknown-order semantics against that pair -- so a new implementation is
    validated by construction, not by re-reading the contract prose.
    """

    def make_order_connector(self) -> tuple[OrderConnector, str, str, str]:
        raise NotImplementedError

    def test_identity_required_without_customer_ref(self) -> None:
        conn, _cust, oid, _status = self.make_order_connector()
        result = conn.lookup_order("demo", None, oid)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "identity_required")
        self.assertIsNone(result.order)

    def test_verified_customer_succeeds(self) -> None:
        conn, cust, oid, status = self.make_order_connector()
        result = conn.lookup_order("demo", cust, oid)
        self.assertTrue(result.ok)
        self.assertEqual(result.code, "ok")
        assert result.order is not None
        self.assertEqual(result.order.id, oid)
        self.assertEqual(result.order.status, status)

    def test_other_customer_does_not_disclose(self) -> None:
        conn, _cust, oid, _status = self.make_order_connector()
        result = conn.lookup_order("demo", "CUST-OTHER-CUSTOMER", oid)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "not_found")
        self.assertIsNone(result.order)

    def test_unknown_order_not_found(self) -> None:
        conn, cust, _oid, _status = self.make_order_connector()
        result = conn.lookup_order("demo", cust, "ORD-NO-SUCH-ORDER")
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "not_found")


class SandboxOrderConformance(OrderConnectorConformanceMixin, unittest.TestCase):
    def setUp(self) -> None:
        self._fixture = _SandboxFixture()
        self.db = self._fixture.db

    def tearDown(self) -> None:
        self._fixture.close()

    def make_order_connector(self) -> tuple[OrderConnector, str, str, str]:
        return SandboxOrderConnector(self.db), "CUST-1001", "ORD-10482", "运输中"


class _ConformanceTransport:
    """Simulates an order service that serves exactly one (customer, order) pair.

    Used to drive the HTTP reference connector through the conformance suite
    without a live server: returns ok for the known pair, not_found for any
    other customer or order, so non-disclosure is enforced server-side.
    """

    def __init__(self, known_customer: str, known_order: str, status: str) -> None:
        self.known_customer = known_customer
        self.known_order = known_order
        self.status = status

    def __call__(
        self, method: str, url: str, params: dict[str, str], headers, timeout
    ) -> tuple[int, dict[str, Any]]:
        order_id = url.rsplit("/orders/", 1)[-1].split("?")[0]
        customer = params.get("customer_ref", "")
        if order_id != self.known_order or customer != self.known_customer:
            return 404, {}
        return 200, {
            "order": {
                "id": order_id,
                "status": self.status,
                "eta": "2026-07-20",
                "tracking_code": "SF1",
            }
        }


_HTTP_CONFIG = HttpConnectorConfig(
    base_url="https://orders.example.com",
    timeout_seconds=5.0,
    signature_secret="shared-secret",
)


class HttpOrderConformance(OrderConnectorConformanceMixin, unittest.TestCase):
    """The Phase 20.3 HTTP reference connector passes the same conformance suite."""

    def make_order_connector(self) -> tuple[OrderConnector, str, str, str]:
        transport = _ConformanceTransport("CUST-1001", "ORD-10482", "运输中")
        conn = HttpOrderConnector(_HTTP_CONFIG, transport=transport)
        return conn, "CUST-1001", "ORD-10482", "运输中"


class KnowledgeConnectorConformanceMixin(_Assertions):
    """Conformance suite any KnowledgeConnector must pass (Phase 20.4).

    Subclasses implement ``make_knowledge_connector`` returning
    ``(connector, known_query, expected_article_id)``.
    """

    def make_knowledge_connector(self) -> tuple[KnowledgeConnector, str, str]:
        raise NotImplementedError

    def test_known_query_returns_grounded_hit(self) -> None:
        conn, query, article_id = self.make_knowledge_connector()
        hits = conn.search("demo", query)
        self.assertGreater(len(hits), 0)
        self.assertTrue(any(hit.article_id == article_id for hit in hits))
        self.assertTrue(all(hit.article_id and hit.title and hit.content for hit in hits))

    def test_empty_query_does_not_raise(self) -> None:
        conn, _query, _article_id = self.make_knowledge_connector()
        hits = conn.search("demo", "")
        self.assertIsInstance(hits, list)


class CRMConnectorConformanceMixin(_Assertions):
    """Conformance suite any CRMConnector must pass (Phase 20.4).

    Subclasses implement ``make_crm_connector`` returning
    ``(connector, known_customer_ref, known_name)``.
    """

    def make_crm_connector(self) -> tuple[CRMConnector, str, str]:
        raise NotImplementedError

    def test_known_customer_resolves(self) -> None:
        conn, customer_ref, name = self.make_crm_connector()
        result = conn.resolve_customer("demo", customer_ref)
        self.assertTrue(result.ok)
        self.assertEqual(result.code, "ok")
        assert result.profile is not None
        self.assertEqual(result.profile.customer_ref, customer_ref)
        self.assertEqual(result.profile.name, name)

    def test_unknown_customer_not_found(self) -> None:
        conn, _customer_ref, _name = self.make_crm_connector()
        result = conn.resolve_customer("demo", "NO-SUCH-CUST")
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "not_found")
        self.assertIsNone(result.profile)


class SandboxKnowledgeConformance(KnowledgeConnectorConformanceMixin, unittest.TestCase):
    def setUp(self) -> None:
        self._fixture = _SandboxFixture()
        self.db = self._fixture.db

    def tearDown(self) -> None:
        self._fixture.close()

    def make_knowledge_connector(self) -> tuple[KnowledgeConnector, str, str]:
        return SandboxKnowledgeConnector(self.db), "配送一般多久能到？", "kb-shipping"


class SandboxCRMConformance(CRMConnectorConformanceMixin, unittest.TestCase):
    def setUp(self) -> None:
        self._fixture = _SandboxFixture()
        self.db = self._fixture.db

    def tearDown(self) -> None:
        self._fixture.close()

    def make_crm_connector(self) -> tuple[CRMConnector, str, str]:
        return SandboxCRMConnector(self.db), "CUST-1001", "林嘉"


class _KnowledgeConformanceTransport:
    def __init__(self, article_id: str, title: str, content: str) -> None:
        self.article_id = article_id
        self.title = title
        self.content = content

    def __call__(
        self, method: str, url: str, params: dict[str, str], headers, timeout
    ) -> tuple[int, dict[str, Any]]:
        query = params.get("q", "")
        if not query:
            return 200, {"hits": []}
        return 200, {
            "hits": [
                {
                    "article_id": self.article_id,
                    "title": self.title,
                    "content": self.content,
                    "score": 8.0,
                    "source_url": "/kb/shipping",
                    "version": "1",
                }
            ]
        }


class HttpKnowledgeConformance(KnowledgeConnectorConformanceMixin, unittest.TestCase):
    def make_knowledge_connector(self) -> tuple[KnowledgeConnector, str, str]:
        transport = _KnowledgeConformanceTransport(
            "kb-shipping", "配送时效", "现货订单付款后 24 小时内出库。"
        )
        conn = HttpKnowledgeConnector(_HTTP_CONFIG, transport=transport)
        return conn, "配送一般多久能到？", "kb-shipping"


class _CRMConformanceTransport:
    def __init__(self, known_customer: str, name: str) -> None:
        self.known_customer = known_customer
        self.name = name

    def __call__(
        self, method: str, url: str, params: dict[str, str], headers, timeout
    ) -> tuple[int, dict[str, Any]]:
        customer = url.rsplit("/customers/", 1)[-1].split("?")[0]
        if customer != self.known_customer:
            return 404, {}
        return 200, {"customer_ref": customer, "name": self.name}


class HttpCRMConformance(CRMConnectorConformanceMixin, unittest.TestCase):
    def make_crm_connector(self) -> tuple[CRMConnector, str, str]:
        from app.connectors_http import HttpCRMConnector

        transport = _CRMConformanceTransport("CUST-1001", "林嘉")
        return HttpCRMConnector(_HTTP_CONFIG, transport=transport), "CUST-1001", "林嘉"


class SandboxKnowledgeAndCRMTests(unittest.TestCase):
    def setUp(self) -> None:
        self._fixture = _SandboxFixture()
        self.db = self._fixture.db
        self.knowledge_connector = SandboxKnowledgeConnector(self.db)
        self.crm_connector = SandboxCRMConnector(self.db)

    def tearDown(self) -> None:
        self._fixture.close()

    def test_sandbox_connectors_satisfy_protocols(self) -> None:
        self.assertIsInstance(SandboxOrderConnector(self.db), OrderConnector)
        self.assertIsInstance(self.knowledge_connector, KnowledgeConnector)
        self.assertIsInstance(self.crm_connector, CRMConnector)

    def test_knowledge_search_returns_grounded_hits(self) -> None:
        hits = self.knowledge_connector.search("demo", "配送一般多久能到？")
        self.assertGreater(len(hits), 0)
        self.assertTrue(all(hit.article_id and hit.title and hit.content for hit in hits))

    def test_crm_resolves_known_and_unknown_customer(self) -> None:
        result = self.crm_connector.resolve_customer("demo", "CUST-1001")
        self.assertTrue(result.ok)
        assert result.profile is not None
        self.assertEqual(result.profile.customer_ref, "CUST-1001")
        self.assertEqual(result.profile.name, "林嘉")
        unknown = self.crm_connector.resolve_customer("demo", "NO-SUCH-CUST")
        self.assertFalse(unknown.ok)
        self.assertEqual(unknown.code, "not_found")


if __name__ == "__main__":
    unittest.main()
