"""Backlog: session intelligent summary (会话智能摘要).

Covers: handoff generates a context brief ("前情摘要") that the detail API
surfaces to the operator; resolve drafts a disposition record ("处置记录草稿");
generation is model-first and falls back to the deterministic projection when
no model provider is available or the model output is unusable; each kind is
generated once so re-opening/re-resolving cannot clobber an earlier draft.
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
from app.summaries import SummaryService

ADMIN_KEY = "summaries-admin-key-001"


def _settings(db_path: Path) -> Settings:
    principals = {ADMIN_KEY: {"tenant_id": "demo", "actor_id": "admin", "role": "admin"}}
    return Settings(
        database_path=db_path,
        auth_mode="api_key",
        api_keys_json=json.dumps(principals),
        rate_limit_per_minute=10000,
        docs_enabled=False,
        turn_worker_enabled=False,
    )


class FakeModelProvider:
    """Deterministic provider stub; ``fail`` flips it into error mode."""

    def __init__(self, text: str = "这是模型生成的前情摘要。", fail: bool = False) -> None:
        self.text = text
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    def complete(self, system_prompt: str, user_prompt: str, model_ref: str | None = None) -> str:
        self.calls.append((system_prompt, user_prompt))
        if self.fail:
            raise RuntimeError("model provider down")
        return json.dumps({"summary": self.text}, ensure_ascii=False)


class SummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "summaries.db"
        self.client = TestClient(create_app(_settings(self.db_path)))
        self.services = cast(Any, self.client.app).state.services
        self.admin = {"X-API-Key": ADMIN_KEY, "X-Tenant-Id": "demo"}

    def tearDown(self) -> None:
        self.services.database.close()
        self.client.close()
        self._tmp.cleanup()

    def _open_conversation(self, name: str = "S") -> str:
        conv = self.client.post(
            "/api/conversations", json={"customer_name": name}, headers=self.admin
        ).json()
        self.client.post(
            f"/api/conversations/{conv['id']}/messages",
            json={"content": "我买的 ORD-10482 什么时候发货"},
            headers={**self.admin, "Idempotency-Key": f"summaries-key-{name}"},
        )
        return conv["id"]

    def _summaries(self, conversation_id: str) -> list[dict[str, Any]]:
        detail = self.client.get(f"/api/conversations/{conversation_id}", headers=self.admin).json()
        return detail.get("summaries", [])

    # ---------------------------------------------------------------- handoff

    def test_handoff_generates_context_summary(self) -> None:
        conversation_id = self._open_conversation("ctx")
        response = self.client.post(
            f"/api/conversations/{conversation_id}/accept", headers=self.admin
        )
        self.assertEqual(response.status_code, 200, response.text)
        summaries = self._summaries(conversation_id)
        context = [s for s in summaries if s["kind"] == "context"]
        self.assertEqual(len(context), 1)
        self.assertEqual(context[0]["source"], "rule")
        self.assertIn("ORD-10482", context[0]["content"])
        self.assertIn("客户 ctx", context[0]["content"])

    def test_detail_surfaces_summary_field(self) -> None:
        conversation_id = self._open_conversation("ctx2")
        self.client.post(f"/api/conversations/{conversation_id}/accept", headers=self.admin)
        detail = self.client.get(f"/api/conversations/{conversation_id}", headers=self.admin).json()
        self.assertIn("summaries", detail)
        self.assertIsInstance(detail["summaries"], list)

    def test_new_conversation_has_no_summary(self) -> None:
        conversation_id = self._open_conversation("none")
        self.assertEqual(self._summaries(conversation_id), [])

    # ---------------------------------------------------------------- resolve

    def test_resolve_drafts_disposition_summary(self) -> None:
        conversation_id = self._open_conversation("disp")
        self.client.post(f"/api/conversations/{conversation_id}/resolve", headers=self.admin)
        summaries = self._summaries(conversation_id)
        disposition = [s for s in summaries if s["kind"] == "disposition"]
        self.assertEqual(len(disposition), 1)
        self.assertEqual(disposition[0]["source"], "rule")
        self.assertIn("ORD-10482", disposition[0]["content"])

    def test_summary_generated_once_after_reopen(self) -> None:
        """Disposition is for a resolved conversation; after reopen+re-resolve
        the existing draft must not be regenerated or clobbered."""
        conversation_id = self._open_conversation("once")
        self.client.post(f"/api/conversations/{conversation_id}/resolve", headers=self.admin)
        first = self._summaries(conversation_id)
        self.client.post(f"/api/conversations/{conversation_id}/reopen", headers=self.admin)
        self.client.post(f"/api/conversations/{conversation_id}/resolve", headers=self.admin)
        second = self._summaries(conversation_id)
        self.assertEqual(
            [s["content"] for s in first if s["kind"] == "disposition"],
            [s["content"] for s in second if s["kind"] == "disposition"],
        )

    # ---------------------------------------------------------------- model path

    def test_model_provider_used_when_available(self) -> None:
        provider = FakeModelProvider("模型产出的处置草稿。")
        service = SummaryService(self.services.database, provider)
        conversation_id = self._open_conversation("model")
        row = service.generate("demo", conversation_id, "context")
        self.assertEqual(row["source"], "model")
        self.assertIn("模型产出的处置草稿", row["content"])
        self.assertEqual(len(provider.calls), 1)

    def test_model_failure_falls_back_to_rule(self) -> None:
        provider = FakeModelProvider(fail=True)
        service = SummaryService(self.services.database, provider)
        conversation_id = self._open_conversation("fail")
        row = service.generate("demo", conversation_id, "context")
        self.assertEqual(row["source"], "rule")
        self.assertIn("ORD-10482", row["content"])

    def test_malformed_model_json_falls_back_to_rule(self) -> None:
        class BadProvider(FakeModelProvider):
            def complete(self, system_prompt: str, user_prompt: str, model_ref=None) -> str:
                return "not json at all"

        service = SummaryService(self.services.database, BadProvider())
        conversation_id = self._open_conversation("badjson")
        row = service.generate("demo", conversation_id, "context")
        self.assertEqual(row["source"], "rule")

    def test_empty_model_summary_falls_back_to_rule(self) -> None:
        class EmptyProvider(FakeModelProvider):
            def complete(self, system_prompt: str, user_prompt: str, model_ref=None) -> str:
                return json.dumps({"summary": "   "})

        service = SummaryService(self.services.database, EmptyProvider())
        conversation_id = self._open_conversation("empty")
        row = service.generate("demo", conversation_id, "context")
        self.assertEqual(row["source"], "rule")

    def test_internal_notes_never_leak_into_summary(self) -> None:
        conversation_id = self._open_conversation("notes")
        self.client.post(
            f"/api/conversations/{conversation_id}/notes",
            json={"content": "内部讨论：客户可能要求退款，先别答应"},
            headers=self.admin,
        )
        with self.services.database.connect() as conn:
            role = conn.execute(
                "SELECT role FROM messages WHERE conversation_id = ? ORDER BY seq DESC LIMIT 1",
                (conversation_id,),
            ).fetchone()["role"]
        self.assertEqual(role, "internal_note")
        self.client.post(f"/api/conversations/{conversation_id}/accept", headers=self.admin)
        context = [s for s in self._summaries(conversation_id) if s["kind"] == "context"]
        self.assertNotIn("先别答应", context[0]["content"])

    def test_handoff_failure_never_blocks_acceptance(self) -> None:
        """A recording bug in summary generation must not break the accept
        lifecycle path (best-effort is the contract)."""
        conversation_id = self._open_conversation("robust")
        original = self.services.orchestrator.summaries.generate
        self.services.orchestrator.summaries.generate = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("boom")
        )
        try:
            response = self.client.post(
                f"/api/conversations/{conversation_id}/accept", headers=self.admin
            )
        finally:
            self.services.orchestrator.summaries.generate = original
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "human_active")


if __name__ == "__main__":
    unittest.main()
