"""Backlog: AI-assisted operator copilot (AI 辅助坐席).

Covers the roadmap acceptance:
- reply suggestions are non-empty with the correct source; model-first with a
  canned-response fallback on any provider failure;
- internal notes never enter the suggestion context;
- knowledge recommendations use the latest customer message (or an explicit
  query) and are language-aware;
- tone rewrites return the original text on failure (never block);
- RBAC: all three copilot endpoints require ``operator:act`` (viewer 403);
- missing conversations 404.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from app.config import Settings
from app.copilot import CopilotService
from app.main import create_app
from app.model_provider import ModelResponse

ADMIN_KEY = "copilot-admin-key-001"
VIEWER_KEY = "copilot-viewer-key-001"


def _settings(db_path: Path) -> Settings:
    principals = {
        ADMIN_KEY: {"tenant_id": "demo", "actor_id": "admin", "role": "admin"},
        VIEWER_KEY: {"tenant_id": "demo", "actor_id": "viewer", "role": "viewer"},
    }
    return Settings(
        database_path=db_path,
        auth_mode="api_key",
        api_keys_json=json.dumps(principals),
        rate_limit_per_minute=10000,
        docs_enabled=False,
        turn_worker_enabled=False,
    )


class FakeModelProvider:
    def __init__(
        self,
        *,
        suggestions: list[str] | None = None,
        rewritten: str = "重写后的专业回复。",
        fail: bool = False,
        bad_json: bool = False,
    ) -> None:
        self.suggestions = suggestions or [
            "这是模型生成的建议回复一。",
            "这是模型生成的建议回复二。",
        ]
        self.rewritten = rewritten
        self.fail = fail
        self.bad_json = bad_json
        self.prompts: list[str] = []

    def complete(
        self, system_prompt: str, user_prompt: str, model_ref: str | None = None
    ) -> ModelResponse:
        self.prompts.append(user_prompt)
        if self.fail:
            raise RuntimeError("model provider down")
        if self.bad_json:
            return ModelResponse(content="not json")
        if "coach" in system_prompt:
            return ModelResponse(
                content=json.dumps({"suggestions": self.suggestions}, ensure_ascii=False)
            )
        return ModelResponse(content=json.dumps({"rewritten": self.rewritten}, ensure_ascii=False))


class CopilotAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "copilot.db"
        self.client = TestClient(create_app(_settings(self.db_path)))
        self.services = cast(Any, self.client.app).state.services
        self.copilot: CopilotService = self.services.copilot
        self.admin = {"X-API-Key": ADMIN_KEY, "X-Tenant-Id": "demo"}
        self.viewer = {"X-API-Key": VIEWER_KEY, "X-Tenant-Id": "demo"}

    def tearDown(self) -> None:
        self.services.database.close()
        self.client.close()
        self._tmp.cleanup()

    def _open_conversation(self, name: str = "S") -> str:
        conv = self.client.post(
            "/api/conversations", json={"customer_name": name}, headers=self.admin
        ).json()
        return conv["id"]

    def _send(self, conversation_id: str, content: str, key: str) -> None:
        response = self.client.post(
            f"/api/conversations/{conversation_id}/messages",
            json={"content": content},
            headers={**self.admin, "Idempotency-Key": key},
        )
        self.assertEqual(response.status_code, 200, response.text)

    def _add_canned(self, title: str, body: str) -> None:
        self.services.database.create_canned_response(
            "demo",
            title=title,
            body=body,
            shortcut=None,
            tags=["policy"],
            actor_id="admin",
        )

    def _add_knowledge(self, title: str, tags: list[str], language: str | None = None) -> None:
        response = self.client.post(
            "/api/knowledge",
            json={
                "title": title,
                "content": f"关于 {title} 的详细说明，供知识库检索使用。",
                "tags": tags,
                "category": "policy",
                "source_url": "https://example.com/policy",
                "language": language,
            },
            headers=self.admin,
        )
        self.assertEqual(response.status_code, 201, response.text)

    # ------------------------------------------------------------- suggestions

    def test_suggest_falls_back_to_canned_without_model(self) -> None:
        self._add_canned("退款政策", "根据当前政策，您可以申请全额退款。")
        conversation_id = self._open_conversation()
        self._send(conversation_id, "我想退款", "copilot-key-1")
        response = self.client.post(
            "/api/copilot/suggest",
            json={"conversation_id": conversation_id},
            headers=self.admin,
        )
        self.assertEqual(response.status_code, 200, response.text)
        suggestions = response.json()["suggestions"]
        self.assertTrue(suggestions)
        self.assertEqual(suggestions[0]["source"], "rule")
        self.assertIn("退款", suggestions[0]["content"])

    def test_suggest_model_first(self) -> None:
        provider = FakeModelProvider(suggestions=["请提供订单号以便核查。"])
        self.copilot.model_provider = provider
        conversation_id = self._open_conversation()
        self._send(conversation_id, "订单丢了", "copilot-key-2")
        response = self.client.post(
            "/api/copilot/suggest",
            json={"conversation_id": conversation_id},
            headers=self.admin,
        )
        suggestions = response.json()["suggestions"]
        self.assertEqual(suggestions[0]["content"], "请提供订单号以便核查。")
        self.assertEqual(suggestions[0]["source"], "model")

    def test_suggest_model_failure_falls_back_to_canned(self) -> None:
        self._add_canned("退款政策", "根据当前政策，您可以申请全额退款。")
        self.copilot.model_provider = FakeModelProvider(fail=True)
        conversation_id = self._open_conversation()
        self._send(conversation_id, "我想退款", "copilot-key-3")
        response = self.client.post(
            "/api/copilot/suggest",
            json={"conversation_id": conversation_id},
            headers=self.admin,
        )
        suggestions = response.json()["suggestions"]
        self.assertTrue(suggestions)
        self.assertEqual(suggestions[0]["source"], "rule")

    def test_suggest_never_leaks_internal_notes(self) -> None:
        provider = FakeModelProvider()
        self.copilot.model_provider = provider
        conversation_id = self._open_conversation()
        self._send(conversation_id, "你好", "copilot-key-4")
        self.client.post(
            f"/api/conversations/{conversation_id}/notes",
            json={"content": "内部机密：客户是 VIP，先稳住不要承诺退款"},
            headers=self.admin,
        )
        self.client.post(
            "/api/copilot/suggest",
            json={"conversation_id": conversation_id},
            headers=self.admin,
        )
        prompt = provider.prompts[-1]
        self.assertNotIn("内部机密", prompt)
        self.assertNotIn("VIP", prompt)

    def test_suggest_unknown_conversation_404(self) -> None:
        response = self.client.post(
            "/api/copilot/suggest",
            json={"conversation_id": "conv_nonexistent"},
            headers=self.admin,
        )
        self.assertEqual(response.status_code, 404, response.text)

    # --------------------------------------------------------- recommendations

    def test_recommend_knowledge_uses_latest_customer_message(self) -> None:
        self._add_knowledge("退换货政策", ["refund"], "zh")
        conversation_id = self._open_conversation()
        self._send(conversation_id, "refund 怎么申请", "copilot-key-5")
        response = self.client.post(
            "/api/copilot/knowledge",
            json={"conversation_id": conversation_id},
            headers=self.admin,
        )
        self.assertEqual(response.status_code, 200, response.text)
        articles = response.json()["articles"]
        self.assertTrue(articles)
        self.assertEqual(articles[0]["title"], "退换货政策")
        self.assertEqual(articles[0]["source"], "rule")

    def test_recommend_knowledge_query_override(self) -> None:
        self._add_knowledge("发票政策", ["invoice"], "zh")
        conversation_id = self._open_conversation()
        response = self.client.post(
            "/api/copilot/knowledge",
            json={"conversation_id": conversation_id, "query": "invoice"},
            headers=self.admin,
        )
        articles = response.json()["articles"]
        self.assertTrue(articles)
        self.assertEqual(articles[0]["title"], "发票政策")

    def test_recommend_knowledge_empty_conversation_returns_empty(self) -> None:
        conversation_id = self._open_conversation()
        response = self.client.post(
            "/api/copilot/knowledge",
            json={"conversation_id": conversation_id},
            headers=self.admin,
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["articles"], [])

    def test_recommend_knowledge_prefers_customer_language(self) -> None:
        self._add_knowledge("中文退换货", ["refund"], "zh")
        self._add_knowledge("Refund policy", ["refund"], "en")
        conversation_id = self._open_conversation()
        # English customer message -> conversation.language = en.
        self._send(conversation_id, "How do I apply for a refund?", "copilot-key-6")
        response = self.client.post(
            "/api/copilot/knowledge",
            json={"conversation_id": conversation_id, "query": "refund"},
            headers=self.admin,
        )
        articles = response.json()["articles"]
        self.assertTrue(articles)
        self.assertEqual(articles[0]["language"], "en")

    # ------------------------------------------------------------------ rewrite

    def test_rewrite_model_first(self) -> None:
        self.copilot.model_provider = FakeModelProvider(
            rewritten="感谢您的耐心等待，我们正在为您核实。"
        )
        response = self.client.post(
            "/api/copilot/rewrite",
            json={"text": "等着", "tone": "friendly"},
            headers=self.admin,
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["source"], "model")
        self.assertEqual(payload["tone"], "friendly")
        self.assertIn("感谢", payload["rewritten"])

    def test_rewrite_failure_returns_original(self) -> None:
        self.copilot.model_provider = FakeModelProvider(fail=True)
        response = self.client.post(
            "/api/copilot/rewrite",
            json={"text": "请等待", "tone": "concise"},
            headers=self.admin,
        )
        payload = response.json()
        self.assertEqual(payload["rewritten"], "请等待")
        self.assertEqual(payload["source"], "rule")

    def test_rewrite_identity_output_treated_as_unchanged(self) -> None:
        self.copilot.model_provider = FakeModelProvider(rewritten="请等待")
        response = self.client.post(
            "/api/copilot/rewrite",
            json={"text": "请等待", "tone": "professional"},
            headers=self.admin,
        )
        self.assertEqual(response.json()["source"], "rule")

    def test_rewrite_empty_text_rejected(self) -> None:
        response = self.client.post(
            "/api/copilot/rewrite",
            json={"text": "   ", "tone": "friendly"},
            headers=self.admin,
        )
        self.assertEqual(response.status_code, 422, response.text)

    # --------------------------------------------------------------------- RBAC

    def test_viewer_denied_all_endpoints(self) -> None:
        conversation_id = self._open_conversation()
        calls = [
            ("/api/copilot/suggest", {"conversation_id": conversation_id}),
            ("/api/copilot/knowledge", {"conversation_id": conversation_id}),
            ("/api/copilot/rewrite", {"text": "你好", "tone": "friendly"}),
        ]
        for path, body in calls:
            response = self.client.post(path, json=body, headers=self.viewer)
            self.assertEqual(response.status_code, 403, f"{path}: {response.text}")


if __name__ == "__main__":
    unittest.main()
