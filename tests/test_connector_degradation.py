"""Phase 20 acceptance: fault injection still degrades safely.

When an external connector is open or exhausted, conversation correctness
must hold: Knowledge falls back to built-in FTS, Order/CRM escalate to a
human without leaking another customer's data, and no duplicate business
operation is issued.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.agents import KnowledgeAgent, OrderAgent
from app.config import Settings
from app.connectors import OrderLookup, SandboxOrderConnector
from app.connectors_runtime import (
    CircuitBreakerConfig,
    CircuitBreakerRegistry,
    ResilientCRMConnector,
    ResilientKnowledgeConnector,
    ResilientOrderConnector,
    TransientConnectorError,
)
from app.database import Database
from app.domain import AgentName
from app.orchestrator import ConversationOrchestrator
from app.tools import ToolGateway


_CONFIG = CircuitBreakerConfig(
    failure_threshold=1,
    recovery_timeout_seconds=60.0,
    retry_max_attempts=1,
    retry_base_backoff_seconds=0.0,
)


class _EmptyKnowledgeConn:
    def search(self, tenant_id, query, *, limit=3):
        return []


class _AlwaysTransientOrderConn:
    def __init__(self) -> None:
        self.calls = 0

    def lookup_order(self, tenant_id, customer_ref, order_id):
        self.calls += 1
        raise TransientConnectorError("timeout")


class _AlwaysTransientCRMConn:
    def __init__(self) -> None:
        self.calls = 0

    def resolve_customer(self, tenant_id, customer_ref):
        self.calls += 1
        raise TransientConnectorError("timeout")


class KnowledgeFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._tmp.name) / "deg.db")
        self.db.initialize()

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def test_empty_connector_falls_back_to_fts(self) -> None:
        agent = KnowledgeAgent(self.db, knowledge_connector=_EmptyKnowledgeConn())
        result = agent.respond("demo", "配送一般多久能到？")
        self.assertEqual(result.agent, AgentName.KNOWLEDGE)
        self.assertFalse(result.requires_human)
        self.assertTrue(result.citations)
        self.assertIn("24 小时", result.content)

    def test_open_breaker_falls_back_to_fts(self) -> None:
        registry = CircuitBreakerRegistry(_CONFIG)
        connector = ResilientKnowledgeConnector(_EmptyKnowledgeConn(), registry)
        breaker = registry.get("demo", "knowledge")
        breaker.record_failure()
        self.assertEqual(breaker.state.value, "open")
        agent = KnowledgeAgent(self.db, knowledge_connector=connector)
        result = agent.respond("demo", "配送一般多久能到？")
        self.assertFalse(result.requires_human)
        self.assertTrue(result.citations)


class OrderUnavailableTests(unittest.TestCase):
    def test_unavailable_escalates_without_leaking_status(self) -> None:
        registry = CircuitBreakerRegistry(_CONFIG)
        inner = _AlwaysTransientOrderConn()
        tools = ToolGateway(
            database=None,  # type: ignore[arg-type]
            order_connector=ResilientOrderConnector(inner, registry),
        )
        result = OrderAgent(tools).respond("demo", "CUST-1001", "帮我查一下 ORD-10482 物流")
        self.assertTrue(result.requires_human)
        self.assertEqual(result.handoff_reason, "Order connector unavailable")
        self.assertNotIn("运输中", result.content)
        self.assertEqual(result.tool_calls[0]["code"], "unavailable")
        self.assertEqual(inner.calls, 1)


class OrchestratorFaultInjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "deg.db"
        self.db = Database(self.db_path)
        self.db.initialize()
        self.settings = Settings(database_path=self.db_path, auth_mode="demo")
        self.orchestrator = ConversationOrchestrator(self.db, self.settings)

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def _conversation(self, customer_ref: str | None = "CUST-1001") -> str:
        return self.db.create_conversation(
            "demo", "Fault Customer", customer_ref, "web", "admin", 120
        )["id"]

    def test_knowledge_golden_path_survives_open_breaker(self) -> None:
        breaker = self.orchestrator.breaker_registry.get("demo", "knowledge")
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_failure()
        conv_id = self._conversation(None)
        response = self.orchestrator.handle_customer_message(
            "demo", conv_id, "配送一般多久能到？", "admin", "idem-kb-fault"
        )
        assistant = response["assistant_message"]
        assert assistant is not None
        self.assertEqual(assistant["metadata"]["agent"], "knowledge")
        self.assertTrue(assistant["metadata"]["citations"])
        self.assertEqual(response["conversation"]["status"], "open")
        self.assertNotIn("转接人工", assistant["content"])

    def test_order_unavailable_escalates_and_does_not_leak(self) -> None:
        self.orchestrator.tools = ToolGateway(
            self.db,
            order_connector=ResilientOrderConnector(
                _AlwaysTransientOrderConn(), self.orchestrator.breaker_registry
            ),
            crm_connector=self.orchestrator.tools._crm_connector,
        )
        self.orchestrator.order = OrderAgent(self.orchestrator.tools)
        conv_id = self._conversation("CUST-1001")
        response = self.orchestrator.handle_customer_message(
            "demo", conv_id, "帮我查一下 ORD-10482 物流", "admin", "idem-ord-fault"
        )
        assistant = response["assistant_message"]
        assert assistant is not None
        self.assertEqual(response["conversation"]["status"], "waiting_human")
        order_call = next(
            call
            for call in assistant["metadata"]["tool_calls"]
            if call.get("tool") == "orders.lookup"
        )
        self.assertEqual(order_call["code"], "unavailable")
        self.assertNotIn("运输中", assistant["content"])

    def test_crm_unavailable_escalates_without_order_lookup(self) -> None:
        inner_order = SandboxOrderConnector(self.db)
        self.orchestrator.tools = ToolGateway(
            self.db,
            order_connector=inner_order,
            crm_connector=ResilientCRMConnector(
                _AlwaysTransientCRMConn(), self.orchestrator.breaker_registry
            ),
        )
        self.orchestrator.order = OrderAgent(self.orchestrator.tools)
        conv_id = self._conversation("CUST-1001")
        response = self.orchestrator.handle_customer_message(
            "demo", conv_id, "帮我查一下 ORD-10482 物流", "admin", "idem-crm-fault"
        )
        assistant = response["assistant_message"]
        assert assistant is not None
        self.assertEqual(response["conversation"]["status"], "waiting_human")
        self.assertEqual(assistant["metadata"]["handoff_reason"], "CRM connector unavailable")
        crm_call = next(
            call
            for call in assistant["metadata"]["tool_calls"]
            if call.get("tool") == "customers.resolve"
        )
        self.assertEqual(crm_call["code"], "unavailable")
        self.assertNotIn("运输中", assistant["content"])

    def test_order_unavailable_does_not_retry_business_call_after_open(self) -> None:
        inner = _AlwaysTransientOrderConn()
        connector = ResilientOrderConnector(inner, CircuitBreakerRegistry(_CONFIG))
        first = connector.lookup_order("demo", "CUST-1001", "ORD-10482")
        second = connector.lookup_order("demo", "CUST-1001", "ORD-10482")
        self.assertEqual(first, OrderLookup(ok=False, code="unavailable"))
        self.assertEqual(second, OrderLookup(ok=False, code="unavailable"))
        self.assertEqual(inner.calls, 1)


if __name__ == "__main__":
    unittest.main()
