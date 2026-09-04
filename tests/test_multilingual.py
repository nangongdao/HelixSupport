"""Backlog: multi-language customer service (多语言客服).

Covers the roadmap acceptance:
- script-based detection maps zh/en/ja/ko/ru/ar/hi/he/th/el correctly;
- detection is model-first and falls back to rules on provider failure,
  malformed JSON, or an unknown code;
- reply translation is model-first; any failure returns the original reply
  marked ``translated=false`` and never blocks the turn;
- retrieval prefers articles in the customer's language (or language-agnostic
  ones) without silently dropping cross-language matches;
- the conversation row and message metadata surface the detected language;
- the knowledge API accepts and persists an article language.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from app.config import Settings
from app.language import LanguageService, detect_language
from app.main import create_app

ADMIN_KEY = "multi-admin-key-001"


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

    def __init__(
        self,
        *,
        detect: str = "en",
        translation: str = "Per our policy: refund approved.",
        fail: bool = False,
        bad_json: bool = False,
    ) -> None:
        self.detect = detect
        self.translation = translation
        self.fail = fail
        self.bad_json = bad_json
        self.calls: list[tuple[str, str]] = []

    def complete(self, system_prompt: str, user_prompt: str, model_ref: str | None = None) -> str:
        self.calls.append((system_prompt, user_prompt))
        if self.fail:
            raise RuntimeError("model provider down")
        if self.bad_json:
            return "not json"
        key = "language" if "language identification" in system_prompt else "translation"
        value = self.detect if key == "language" else self.translation
        return json.dumps({key: value}, ensure_ascii=False)


class DetectionTests(unittest.TestCase):
    def test_script_based_detection(self) -> None:
        cases = {
            "中文订单": "zh",
            "Hello world": "en",
            "こんにちは": "ja",
            "안녕하세요": "ko",
            "Привет": "ru",
            "مرحبا": "ar",
            "नमस्ते": "hi",
            "שלום": "he",
            "สวัสดี": "th",
            "γεια": "el",
        }
        for text, expected in cases.items():
            self.assertEqual(detect_language(text), expected, text)

    def test_no_signal_returns_none(self) -> None:
        self.assertIsNone(detect_language(""))
        self.assertIsNone(detect_language("   "))
        self.assertIsNone(detect_language("!!! ??? ---"))

    def test_japanese_kana_beats_shared_han(self) -> None:
        # Kana only occurs in Japanese, so kana+kanji text is Japanese even
        # though the Han ideographs are shared with Chinese.
        self.assertEqual(detect_language("日本語テスト"), "ja")
        self.assertEqual(detect_language("これはテストです"), "ja")

    def test_latin_defaults_to_en(self) -> None:
        self.assertEqual(detect_language("A"), "en")


class LanguageServiceTests(unittest.TestCase):
    def test_model_first_detection(self) -> None:
        provider = FakeModelProvider(detect="fr")
        service = LanguageService(provider, "zh")
        self.assertEqual(service.detect("Bonjour"), ("fr", "model"))
        self.assertEqual(len(provider.calls), 1)

    def test_detection_failure_falls_back_to_rules(self) -> None:
        service = LanguageService(FakeModelProvider(fail=True), "zh")
        language, source = service.detect("你好")
        self.assertEqual(language, "zh")
        self.assertEqual(source, "rule")

    def test_detection_unknown_code_falls_back_to_rules(self) -> None:
        service = LanguageService(FakeModelProvider(detect="xx"), "zh")
        language, source = service.detect("Hello")
        self.assertEqual(language, "en")
        self.assertEqual(source, "rule")

    def test_detection_malformed_json_falls_back_to_rules(self) -> None:
        service = LanguageService(FakeModelProvider(bad_json=True), "zh")
        language, source = service.detect("Hello")
        self.assertEqual(language, "en")
        self.assertEqual(source, "rule")

    def test_detection_without_provider_uses_rules(self) -> None:
        service = LanguageService(None, "zh")
        self.assertEqual(service.detect("你好"), ("zh", "rule"))

    def test_translate_model_success(self) -> None:
        provider = FakeModelProvider(translation="Hello, how can I help?")
        service = LanguageService(provider, "zh")
        text, translated, source = service.translate("你好，请问需要什么帮助？", "en")
        self.assertEqual(text, "Hello, how can I help?")
        self.assertTrue(translated)
        self.assertEqual(source, "model")

    def test_translate_same_language_skips_model(self) -> None:
        provider = FakeModelProvider()
        service = LanguageService(provider, "zh")
        text, translated, source = service.translate("你好", "zh")
        self.assertEqual(text, "你好")
        self.assertFalse(translated)
        self.assertEqual(source, "none")
        self.assertEqual(provider.calls, [])

    def test_translate_failure_returns_original(self) -> None:
        service = LanguageService(FakeModelProvider(fail=True), "zh")
        text, translated, source = service.translate("你好", "en")
        self.assertEqual(text, "你好")
        self.assertFalse(translated)
        self.assertEqual(source, "rule")

    def test_translate_without_provider_returns_original(self) -> None:
        service = LanguageService(None, "zh")
        text, translated, source = service.translate("你好", "en")
        self.assertEqual(text, "你好")
        self.assertFalse(translated)
        self.assertEqual(source, "rule")

    def test_translate_identity_output_treated_as_untouched(self) -> None:
        provider = FakeModelProvider(translation="你好")
        service = LanguageService(provider, "zh")
        _text, translated, source = service.translate("你好", "en")
        self.assertFalse(translated)
        self.assertEqual(source, "rule")


class MultilingualAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "multi.db"
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
        return conv["id"]

    def _send(self, conversation_id: str, content: str, key: str) -> dict[str, Any]:
        response = self.client.post(
            f"/api/conversations/{conversation_id}/messages",
            json={"content": content},
            headers={**self.admin, "Idempotency-Key": key},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_customer_message_language_detected_and_persisted(self) -> None:
        conversation_id = self._open_conversation()
        turn = self._send(conversation_id, "Hello, can you help me?", "multi-key-1")
        self.assertEqual(turn["conversation"]["language"], "en")
        self.assertEqual(turn["customer_message"]["metadata"]["language"], "en")
        self.assertEqual(turn["customer_message"]["metadata"]["language_source"], "rule")

    def test_chinese_message_stays_zh(self) -> None:
        conversation_id = self._open_conversation()
        turn = self._send(conversation_id, "你好，我的订单什么时候发货？", "multi-key-2")
        self.assertEqual(turn["conversation"]["language"], "zh")
        # Same-language replies are not translated.
        assistant = turn["assistant_message"]
        self.assertEqual(assistant["metadata"].get("translated"), False)
        self.assertEqual(assistant["metadata"].get("translation_source"), "none")

    def test_reply_translated_to_customer_language(self) -> None:
        provider = FakeModelProvider(translation="How may I help you today?")
        self.services.orchestrator.languages = LanguageService(provider, "zh")
        conversation_id = self._open_conversation()
        turn = self._send(conversation_id, "Hello there", "multi-key-3")
        assistant = turn["assistant_message"]
        self.assertEqual(assistant["content"], "How may I help you today?")
        self.assertTrue(assistant["metadata"]["translated"])
        self.assertEqual(assistant["metadata"]["language"], "en")
        self.assertEqual(assistant["metadata"]["translation_source"], "model")
        self.assertIn("original_content", assistant["metadata"])
        self.assertNotEqual(assistant["metadata"]["original_content"], "How may I help you today?")
        self.assertTrue(assistant["metadata"]["original_content"])

    def test_translation_failure_never_blocks_turn(self) -> None:
        provider = FakeModelProvider(fail=True)
        self.services.orchestrator.languages = LanguageService(provider, "zh")
        conversation_id = self._open_conversation()
        turn = self._send(conversation_id, "Hello there", "multi-key-4")
        assistant = turn["assistant_message"]
        self.assertEqual(assistant["metadata"]["language"], "en")
        self.assertFalse(assistant["metadata"]["translated"])
        self.assertEqual(assistant["metadata"]["translation_source"], "rule")
        self.assertNotIn("original_content", assistant["metadata"])

    def test_translation_audit_emitted(self) -> None:
        provider = FakeModelProvider(translation="How may I help you today?")
        self.services.orchestrator.languages = LanguageService(provider, "zh")
        conversation_id = self._open_conversation()
        self._send(conversation_id, "Hello there", "multi-key-5")
        with self.services.database.connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM audit_events WHERE event_type = 'reply.translated'"
            ).fetchone()
        self.assertIsNotNone(row)
        payload = json.loads(row["payload_json"])
        self.assertEqual(payload["to_language"], "en")
        self.assertTrue(payload["translated"])

    def test_manual_language_override_drives_reply_translation(self) -> None:
        # Backlog (多语言客服): the operator's manual override (PATCH /language)
        # pins the reply translation target. A Chinese message sent on a
        # conversation pinned to English gets detected as zh but the assistant
        # reply is translated into the pinned target (en) when a model is
        # configured. Detection is model-first here, so the provider's detect
        # output (zh) is what surfaces as detected_language.
        provider = FakeModelProvider(detect="zh", translation="How may I help you today?")
        self.services.orchestrator.languages = LanguageService(provider, "zh")
        conversation_id = self._open_conversation()
        response = self.client.patch(
            f"/api/conversations/{conversation_id}/language",
            json={"language": "en"},
            headers=self.admin,
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["language"], "en")
        turn = self._send(conversation_id, "你好", "multi-key-override")
        assistant = turn["assistant_message"]
        # The override wins for both the badge and the reply translation
        # target; detection is still recorded separately for observability.
        self.assertEqual(assistant["metadata"]["language"], "en")
        self.assertEqual(assistant["metadata"]["detected_language"], "zh")
        self.assertTrue(assistant["metadata"]["translated"])
        self.assertEqual(assistant["metadata"]["translation_source"], "model")
        # The pinned override is sticky across the message.
        self.assertEqual(turn["conversation"]["language"], "en")
        self.assertEqual(assistant["content"], "How may I help you today?")

    def test_manual_language_override_is_sticky_across_messages(self) -> None:
        # A follow-up message does not let auto-detection overwrite the
        # operator's pinned value.
        conversation_id = self._open_conversation()
        self.client.patch(
            f"/api/conversations/{conversation_id}/language",
            json={"language": "ja"},
            headers=self.admin,
        )
        self._send(conversation_id, "你好", "multi-key-sticky-1")
        turn = self._send(conversation_id, "你好世界", "multi-key-sticky-2")
        self.assertEqual(turn["conversation"]["language"], "ja")

    def test_manual_language_override_clear_restores_auto_detection(self) -> None:
        # Clearing the override (language=null) lets the next customer message
        # re-detect and persist the detected language.
        conversation_id = self._open_conversation()
        self.client.patch(
            f"/api/conversations/{conversation_id}/language",
            json={"language": "en"},
            headers=self.admin,
        )
        self.client.patch(
            f"/api/conversations/{conversation_id}/language",
            json={"language": None},
            headers=self.admin,
        )
        turn = self._send(conversation_id, "你好世界", "multi-key-clear")
        # Detection picks zh now that no override is set.
        self.assertEqual(turn["conversation"]["language"], "zh")
        self.assertEqual(turn["customer_message"]["metadata"]["language"], "zh")


class KnowledgeLanguageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "kb-multi.db"
        self.client = TestClient(create_app(_settings(self.db_path)))
        self.services = cast(Any, self.client.app).state.services
        self.admin = {"X-API-Key": ADMIN_KEY, "X-Tenant-Id": "demo"}

    def tearDown(self) -> None:
        self.services.database.close()
        self.client.close()
        self._tmp.cleanup()

    def _create_article(self, title: str, tags: list[str], language: str | None) -> dict[str, Any]:
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
        return response.json()

    def test_create_article_persists_language(self) -> None:
        article = self._create_article("退换货政策", ["refund"], "zh")
        self.assertEqual(article["language"], "zh")

    def test_language_agnostic_article_has_null_language(self) -> None:
        article = self._create_article("通用政策", ["general"], None)
        self.assertIsNone(article["language"])

    def test_invalid_language_rejected(self) -> None:
        response = self.client.post(
            "/api/knowledge",
            json={
                "title": "坏语言",
                "content": "这是一段足够长的内容用于创建知识条目。",
                "tags": ["bad"],
                "category": "policy",
                "source_url": "https://example.com/policy",
                "language": "xx",
            },
            headers=self.admin,
        )
        self.assertEqual(response.status_code, 422, response.text)

    def test_update_article_language(self) -> None:
        article = self._create_article("退换货政策", ["refund"], "zh")
        response = self.client.patch(
            f"/api/knowledge/{article['id']}",
            json={"language": "en"},
            headers=self.admin,
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["language"], "en")

    def test_search_prefers_customer_language(self) -> None:
        self._create_article("中文退换货", ["refund"], "zh")
        self._create_article("Refund policy", ["refund"], "en")
        database = self.services.database
        zh_hits = database.search_knowledge("demo", "refund", limit=3, language="zh")
        self.assertTrue(zh_hits)
        self.assertEqual(zh_hits[0]["language"], "zh")
        en_hits = database.search_knowledge("demo", "refund", limit=3, language="en")
        self.assertTrue(en_hits)
        self.assertEqual(en_hits[0]["language"], "en")

    def test_language_agnostic_article_matches_any_language(self) -> None:
        self._create_article("通用政策", ["refund"], None)
        self._create_article("Refund policy", ["refund"], "en")
        hits = self.services.database.search_knowledge("demo", "refund", limit=3, language="zh")
        self.assertTrue(hits)
        # The language-agnostic article is preferred over the English one for
        # a Chinese customer (same preference bucket as a zh article).
        self.assertIn(hits[0]["language"], (None, "zh"))


if __name__ == "__main__":
    unittest.main()
