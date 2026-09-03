"""Phase 21: supervisor quality dashboard and knowledge lifecycle.

Covers the three Phase-21 capabilities the ROADMAP_1_X demands:
- 21.1 incremental quality aggregates (``quality_daily`` upsert, supervisor
  listing with keyset pagination, feedback-rating reflow that keeps the
  negative count in sync without re-scanning transcripts).
- 21.2 the supervisor quality/knowledge-gaps REST surface (RBAC, cursor
  pagination, validation, headers).
- 21.3 the knowledge lifecycle: draft -> published/retired with an
  approval gate that cannot be bypassed, retrieval that never returns
  drafts, and negative-feedback-to-draft reflow.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from app.config import Settings
from app.database import Database, utc_now
from app.main import create_app
from app.quality import (
    QualityService,
    decode_quality_cursor,
    encode_quality_cursor,
    estimate_tokens,
    normalize_intent,
    normalize_prompt_version,
)

ADMIN_KEY = "phase21-admin-key-001"
VIEWER_KEY = "phase21-viewer-key-001"
OPERATOR_KEY = "phase21-op-key-0000001"
OTHER_ADMIN_KEY = "phase21-other-admin-001"


def _principals() -> dict[str, dict[str, str]]:
    return {
        ADMIN_KEY: {"tenant_id": "demo", "actor_id": "admin.user", "role": "admin"},
        VIEWER_KEY: {"tenant_id": "demo", "actor_id": "viewer.user", "role": "viewer"},
        OPERATOR_KEY: {
            "tenant_id": "demo",
            "actor_id": "operator.user",
            "role": "operator",
        },
        OTHER_ADMIN_KEY: {
            "tenant_id": "other-tenant",
            "actor_id": "other.admin",
            "role": "admin",
        },
    }


def _settings(db_path: Path) -> Settings:
    return Settings(
        database_path=db_path,
        auth_mode="api_key",
        api_keys_json=json.dumps(_principals()),
        rate_limit_per_minute=10000,
        docs_enabled=False,
    )


def _send_message(
    client: TestClient, headers: dict[str, str], content: str, idx: int = 0
) -> dict[str, Any]:
    """Create a conversation, send one customer message, return the turn body."""
    conv = client.post(
        "/api/conversations",
        json={"customer_name": "phase21", "channel": "web", "customer_ref": "CUST-1001"},
        headers=headers,
    ).json()
    return cast(
        dict[str, Any],
        client.post(
            f"/api/conversations/{conv['id']}/messages",
            json={"content": content},
            headers={**headers, "Idempotency-Key": f"phase21-{idx}-{conv['id'][-8:]}"},
        ).json(),
    )


# ---------------------------------------------------------------------------
# 21.1 QualityService: incremental aggregates & feedback reflow
# ---------------------------------------------------------------------------


class QualityServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "quality.db"
        self.db = Database(self.db_path)
        self.db.initialize()
        self.db.ensure_tenant("demo")
        self.service = QualityService(self.db)

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def _seed_turn_bucket(self) -> None:
        """Create one conversation + assistant message so feedback can attach."""
        conv = self.db.create_conversation("demo", "C", "CUST-1001", "web", "actor", 120)
        self.db.add_message(
            "demo",
            conv["id"],
            "assistant",
            "order",
            "order answer",
            metadata={"intent": "order_status", "prompt_version": "v1"},
        )
        # record_feedback needs a real message id; fetch it back.
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT id FROM messages WHERE tenant_id=? ORDER BY created_at DESC LIMIT 1",
                ("demo",),
            ).fetchone()
        self.message_id = row["id"]
        self.conversation_id = conv["id"]
        self.db.record_feedback("demo", conv["id"], self.message_id, "admin.user", 1, None)
        self.service.record_turn(
            "demo",
            intent="order_status",
            prompt_version="v1",
            escalated=False,
            latency_ms=120,
            first_response_seconds=4.0,
            estimated_tokens=40,
            date_str="2026-01-02",
        )

    def test_record_turn_upserts_bucket(self) -> None:
        self._seed_turn_bucket()
        self.service.record_turn(
            "demo",
            intent="order_status",
            prompt_version="v1",
            escalated=True,
            latency_ms=80,
            first_response_seconds=2.0,
            estimated_tokens=10,
            date_str="2026-01-02",
        )
        buckets = self.service.list_buckets("demo")
        self.assertEqual(len(buckets), 1)
        b = buckets[0]
        self.assertEqual(b["turn_count"], 2)
        self.assertEqual(b["escalation_count"], 1)
        self.assertAlmostEqual(b["avg_first_response_seconds"], 3.0)
        self.assertEqual(b["avg_latency_ms"], 100)
        self.assertEqual(b["estimated_tokens"], 50)

    def test_record_turn_normalizes_missing_dimensions(self) -> None:
        self.service.record_turn(
            "demo",
            intent=None,
            prompt_version=None,
            escalated=False,
            latency_ms=10,
            first_response_seconds=None,
            estimated_tokens=5,
            date_str="2026-01-02",
        )
        b = self.service.list_buckets("demo")[0]
        self.assertEqual(b["intent"], "unknown")
        self.assertEqual(b["prompt_version"], "default")
        self.assertEqual(b["avg_first_response_seconds"], 0.0)

    def test_feedback_reflow_increments_and_decrements_negative_count(self) -> None:
        self._seed_turn_bucket()
        # No previous negative feedback -> -1 increments.
        self.service.apply_feedback_rating(
            "demo",
            message_id=self.message_id,
            actor="admin.user",
            new_rating=-1,
            previous_rating=1,
            intent="order_status",
            prompt_version="v1",
            date_str="2026-01-02",
        )
        b = self.service.list_buckets("demo")[0]
        self.assertEqual(b["negative_feedback_count"], 1)
        # Flip back to positive -> -1 decrements.
        self.service.apply_feedback_rating(
            "demo",
            message_id=self.message_id,
            actor="admin.user",
            new_rating=1,
            previous_rating=-1,
            intent="order_status",
            prompt_version="v1",
            date_str="2026-01-02",
        )
        b = self.service.list_buckets("demo")[0]
        self.assertEqual(b["negative_feedback_count"], 0)

    def test_get_feedback_rating_reads_current_value(self) -> None:
        self._seed_turn_bucket()
        self.assertEqual(self.service.get_feedback_rating("demo", self.message_id, "admin.user"), 1)
        self.assertIsNone(self.service.get_feedback_rating("demo", self.message_id, "nobody"))

    def test_list_buckets_keyset_pagination(self) -> None:
        # Two distinct buckets on the same day.
        for intent, version in (("order_status", "v1"), ("order_status", "v2")):
            self.service.record_turn(
                "demo",
                intent=intent,
                prompt_version=version,
                escalated=False,
                latency_ms=10,
                first_response_seconds=None,
                estimated_tokens=1,
                date_str="2026-01-02",
            )
        page1 = self.service.list_buckets("demo", limit=1)
        self.assertEqual(len(page1), 1)
        last = page1[-1]
        cursor = (last["date"], last["intent"], last["prompt_version"])
        page2 = self.service.list_buckets("demo", cursor=cursor, limit=1)
        self.assertEqual(len(page2), 1)
        self.assertNotEqual(page1[0]["prompt_version"], page2[0]["prompt_version"])

    def test_list_buckets_filters(self) -> None:
        self.service.record_turn(
            "demo",
            intent="order_status",
            prompt_version="v1",
            escalated=False,
            latency_ms=1,
            first_response_seconds=None,
            estimated_tokens=1,
            date_str="2026-01-02",
        )
        self.service.record_turn(
            "demo",
            intent="policy_question",
            prompt_version="v1",
            escalated=False,
            latency_ms=1,
            first_response_seconds=None,
            estimated_tokens=1,
            date_str="2026-01-03",
        )
        only_order = self.service.list_buckets("demo", intent="order_status")
        self.assertEqual(len(only_order), 1)
        self.assertEqual(only_order[0]["intent"], "order_status")
        ranged = self.service.list_buckets("demo", since="2026-01-03")
        self.assertEqual(len(ranged), 1)
        self.assertEqual(ranged[0]["date"], "2026-01-03")
        ranged2 = self.service.list_buckets("demo", until="2026-01-02")
        self.assertEqual(len(ranged2), 1)
        self.assertEqual(ranged2[0]["date"], "2026-01-02")

    def test_list_buckets_rejects_bad_date(self) -> None:
        with self.assertRaises(ValueError):
            self.service.list_buckets("demo", since="notadate")


class QualityCursorTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        cursor = encode_quality_cursor("2026-01-02", "order_status", "v1")
        self.assertEqual(
            decode_quality_cursor(cursor),
            ("2026-01-02", "order_status", "v1"),
        )

    def test_invalid_cursor_raises(self) -> None:
        from app.pagination import InvalidCursorError

        with self.assertRaises(InvalidCursorError):
            decode_quality_cursor("not-a-cursor")


class QualityHelperTests(unittest.TestCase):
    def test_estimate_tokens(self) -> None:
        self.assertEqual(estimate_tokens(""), 0)
        self.assertGreater(estimate_tokens("hello world"), 0)

    def test_normalize(self) -> None:
        self.assertEqual(normalize_intent(None), "unknown")
        self.assertEqual(normalize_prompt_version(""), "default")
        long = "x" * 200
        self.assertEqual(len(normalize_intent(long)), 80)


# ---------------------------------------------------------------------------
# 21.2 supervisor quality REST surface
# ---------------------------------------------------------------------------


class QualityApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "q.db"
        self.client = TestClient(create_app(_settings(self.db_path)))
        self.services = cast(Any, self.client.app).state.services
        self.admin = {"X-API-Key": ADMIN_KEY, "X-Tenant-Id": "demo"}
        self.viewer = {"X-API-Key": VIEWER_KEY, "X-Tenant-Id": "demo"}
        self.operator = {"X-API-Key": OPERATOR_KEY, "X-Tenant-Id": "demo"}

    def tearDown(self) -> None:
        # Close the DB pool explicitly (Windows holds file handles
        # otherwise) before removing the temp directory.
        self.services.database.close()
        self.client.close()
        self._tmp.cleanup()

    def test_operator_forbidden(self) -> None:
        # operator role lacks metrics:read
        r = self.client.get("/api/supervisor/quality", headers=self.operator)
        self.assertEqual(r.status_code, 403)

    def test_viewer_can_list_empty(self) -> None:
        r = self.client.get("/api/supervisor/quality", headers=self.viewer)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), [])
        self.assertEqual(r.headers.get("X-Has-More"), "false")
        self.assertNotIn("X-Next-Cursor", r.headers)

    def test_quality_buckets_after_turns_and_pagination(self) -> None:
        # Generate two turns (same bucket) for one published bucket row.
        _send_message(self.client, self.admin, "帮我查一下订单", idx=0)
        _send_message(self.client, self.admin, "帮我查一下订单", idx=1)
        r = self.client.get("/api/supervisor/quality", headers=self.viewer)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["turn_count"], 2)
        # With only one bucket row, a limit of 1 still leaves no next page:
        # the X-Has-More header is conservative (it is set when the page is
        # full) so we assert the page contents and cursor linkage instead.
        page = self.client.get("/api/supervisor/quality?limit=1", headers=self.viewer)
        self.assertEqual(page.status_code, 200)
        self.assertEqual(len(page.json()), 1)
        # The next cursor points past the single bucket; following it yields
        # an empty page with has-more false.
        next_cursor = page.headers.get("X-Next-Cursor")
        self.assertIsNotNone(next_cursor)
        next_page = self.client.get(
            f"/api/supervisor/quality?limit=1&cursor={next_cursor}",
            headers=self.viewer,
        )
        self.assertEqual(next_page.status_code, 200)
        self.assertEqual(next_page.json(), [])
        self.assertEqual(next_page.headers.get("X-Has-More"), "false")

    def test_invalid_cursor_returns_400(self) -> None:
        r = self.client.get("/api/supervisor/quality?cursor=garbage", headers=self.viewer)
        self.assertEqual(r.status_code, 400)

    def test_invalid_date_returns_400(self) -> None:
        r = self.client.get("/api/supervisor/quality?since=notadate", headers=self.viewer)
        self.assertEqual(r.status_code, 400)

    def test_negative_feedback_updates_aggregate_via_api(self) -> None:
        turn = _send_message(self.client, self.admin, "帮我查一下订单", idx=0)
        aid = turn["assistant_message"]["id"]
        cid = turn["conversation"]["id"]
        # Rate negative -> negative_feedback_count becomes 1.
        fb = self.client.post(
            f"/api/conversations/{cid}/feedback",
            json={"message_id": aid, "rating": -1},
            headers=self.admin,
        )
        self.assertEqual(fb.status_code, 200)
        bucket = self.client.get("/api/supervisor/quality", headers=self.viewer).json()[0]
        self.assertEqual(bucket["negative_feedback_count"], 1)
        # Flip to positive -> decrements back to 0.
        self.client.post(
            f"/api/conversations/{cid}/feedback",
            json={"message_id": aid, "rating": 1},
            headers=self.admin,
        )
        bucket = self.client.get("/api/supervisor/quality", headers=self.viewer).json()[0]
        self.assertEqual(bucket["negative_feedback_count"], 0)

    def test_knowledge_gaps_viewer_ok_operator_forbidden(self) -> None:
        turn = _send_message(self.client, self.admin, "帮我查一下订单", idx=0)
        aid = turn["assistant_message"]["id"]
        cid = turn["conversation"]["id"]
        self.client.post(
            f"/api/conversations/{cid}/feedback",
            json={"message_id": aid, "rating": -1},
            headers=self.admin,
        )
        gaps = self.client.get("/api/supervisor/knowledge-gaps", headers=self.viewer)
        self.assertEqual(gaps.status_code, 200)
        self.assertEqual(len(gaps.json()), 1)
        self.assertEqual(gaps.json()[0]["message_id"], aid)
        forbidden = self.client.get("/api/supervisor/knowledge-gaps", headers=self.operator)
        self.assertEqual(forbidden.status_code, 403)

    def test_tenant_isolation(self) -> None:
        other = {"X-API-Key": OTHER_ADMIN_KEY, "X-Tenant-Id": "other-tenant"}
        _send_message(self.client, self.admin, "帮我查一下订单", idx=0)
        # demo tenant has data; other tenant does not.
        own = self.client.get("/api/supervisor/quality", headers=self.admin).json()
        cross = self.client.get("/api/supervisor/quality", headers=other).json()
        self.assertGreaterEqual(len(own), 1)
        self.assertEqual(cross, [])


# ---------------------------------------------------------------------------
# 21.3 knowledge lifecycle
# ---------------------------------------------------------------------------


class KnowledgeLifecycleApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "k.db"
        self.client = TestClient(create_app(_settings(self.db_path)))
        self.services = cast(Any, self.client.app).state.services
        self.admin = {"X-API-Key": ADMIN_KEY, "X-Tenant-Id": "demo"}
        self.operator = {"X-API-Key": OPERATOR_KEY, "X-Tenant-Id": "demo"}

    def tearDown(self) -> None:
        self.services.database.close()
        self.client.close()
        self._tmp.cleanup()

    _DRAFT = {
        "title": "Draft Answer",
        "content": "A draft body long enough to pass validation.",
        "tags": ["shipping"],
        "category": "general",
        "source_url": "https://example.com/source",
    }

    def test_operator_cannot_create_draft(self) -> None:
        r = self.client.post("/api/knowledge/drafts", json=self._DRAFT, headers=self.operator)
        self.assertEqual(r.status_code, 403)

    def test_only_writers_can_list_inactive_articles(self) -> None:
        draft = self.client.post(
            "/api/knowledge/drafts", json=self._DRAFT, headers=self.admin
        ).json()

        complete = self.client.get("/api/knowledge?include_inactive=true", headers=self.admin)
        self.assertEqual(complete.status_code, 200, complete.text)
        self.assertIn(draft["id"], [article["id"] for article in complete.json()])

        forbidden = self.client.get("/api/knowledge?include_inactive=true", headers=self.operator)
        self.assertEqual(forbidden.status_code, 403, forbidden.text)
        published_only = self.client.get("/api/knowledge", headers=self.operator)
        self.assertEqual(published_only.status_code, 200, published_only.text)
        self.assertNotIn(draft["id"], [article["id"] for article in published_only.json()])

    def test_draft_is_invisible_until_published(self) -> None:
        created = self.client.post("/api/knowledge/drafts", json=self._DRAFT, headers=self.admin)
        self.assertEqual(created.status_code, 201)
        body = created.json()
        self.assertEqual(body["status"], "draft")
        self.assertFalse(body["active"])
        draft_id = body["id"]
        # Not in the public knowledge listing.
        listed = self.client.get("/api/knowledge", headers=self.admin).json()
        self.assertFalse(any(k["id"] == draft_id for k in listed))
        # Not retrievable via search either.
        searched = self.client.get("/api/knowledge?q=shipping", headers=self.admin).json()
        self.assertFalse(any(k["id"] == draft_id for k in searched))

    def test_publish_makes_draft_retrievable(self) -> None:
        draft_id = self.client.post(
            "/api/knowledge/drafts", json=self._DRAFT, headers=self.admin
        ).json()["id"]
        pub = self.client.post(
            f"/api/knowledge/{draft_id}/review",
            json={"action": "publish", "notes": "approved"},
            headers=self.admin,
        )
        self.assertEqual(pub.status_code, 200)
        self.assertEqual(pub.json()["status"], "published")
        self.assertTrue(pub.json()["active"])
        listed = self.client.get("/api/knowledge", headers=self.admin).json()
        self.assertTrue(any(k["id"] == draft_id for k in listed))

    def test_publish_cannot_be_bypassed(self) -> None:
        draft_id = self.client.post(
            "/api/knowledge/drafts", json=self._DRAFT, headers=self.admin
        ).json()["id"]
        self.client.post(
            f"/api/knowledge/{draft_id}/review",
            json={"action": "publish"},
            headers=self.admin,
        )
        # Second publish attempt is rejected with 409.
        second = self.client.post(
            f"/api/knowledge/{draft_id}/review",
            json={"action": "publish"},
            headers=self.admin,
        )
        self.assertEqual(second.status_code, 409)

    def test_retire_and_re_retire(self) -> None:
        draft_id = self.client.post(
            "/api/knowledge/drafts", json=self._DRAFT, headers=self.admin
        ).json()["id"]
        self.client.post(
            f"/api/knowledge/{draft_id}/review",
            json={"action": "publish"},
            headers=self.admin,
        )
        retire = self.client.post(
            f"/api/knowledge/{draft_id}/review",
            json={"action": "retire"},
            headers=self.admin,
        )
        self.assertEqual(retire.status_code, 200)
        self.assertEqual(retire.json()["status"], "retired")
        self.assertFalse(retire.json()["active"])
        # Retiring again is rejected.
        again = self.client.post(
            f"/api/knowledge/{draft_id}/review",
            json={"action": "retire"},
            headers=self.admin,
        )
        self.assertEqual(again.status_code, 409)

    def test_review_unknown_article_404(self) -> None:
        r = self.client.post(
            "/api/knowledge/does-not-exist/review",
            json={"action": "publish"},
            headers=self.admin,
        )
        self.assertEqual(r.status_code, 404)

    def test_draft_from_negative_feedback(self) -> None:
        turn = _send_message(self.client, self.admin, "帮我查一下订单", idx=0)
        aid = turn["assistant_message"]["id"]
        cid = turn["conversation"]["id"]
        self.client.post(
            f"/api/conversations/{cid}/feedback",
            json={"message_id": aid, "rating": -1},
            headers=self.admin,
        )
        gaps = self.client.get("/api/supervisor/knowledge-gaps", headers=self.admin).json()
        self.assertEqual(len(gaps), 1)
        draft = self.client.post(
            f"/api/conversations/{cid}/messages/{aid}/knowledge-draft",
            headers=self.admin,
        )
        self.assertEqual(draft.status_code, 201)
        body = draft.json()
        self.assertEqual(body["status"], "draft")
        self.assertFalse(body["active"])
        self.assertIn("order_status", body["title"])

    def test_draft_from_feedback_unknown_message_404(self) -> None:
        turn = _send_message(self.client, self.admin, "帮我查一下订单", idx=0)
        cid = turn["conversation"]["id"]
        r = self.client.post(
            f"/api/conversations/{cid}/messages/no-such-msg/knowledge-draft",
            headers=self.admin,
        )
        self.assertEqual(r.status_code, 404)


class KnowledgeLifecycleDbTests(unittest.TestCase):
    """Direct Database-level lifecycle guarantees (no HTTP layer)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._tmp.name) / "k.db")
        self.db.initialize()
        self.db.ensure_tenant("demo")

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def test_published_article_with_null_status_still_searchable(self) -> None:
        """Legacy rows (status IS NULL) must remain visible after migration 9."""
        # Insert a legacy article with no status column value (NULL).
        now = utc_now()
        with self.db.connect() as conn:
            conn.execute(
                """INSERT INTO knowledge_articles
                (id, tenant_id, title, content, tags, category, source_url,
                 active, version, updated_at)
                VALUES ('kb_legacy', 'demo', 'Legacy', 'body content here',
                 'shipping', 'general', 'https://e.com', 1, 1, ?)""",
                (now,),
            )
            conn.commit()
        results = self.db.search_knowledge("demo", "shipping")
        self.assertTrue(any(r["id"] == "kb_legacy" for r in results))

    def test_draft_not_searchable_until_published(self) -> None:
        article = self.db.create_knowledge_draft(
            "demo",
            "Title",
            "Body content",
            ["shipping"],
            "general",
            "https://e.com",
            "admin",
        )
        self.assertEqual(article["status"], "draft")
        # The draft must not appear in retrieval or listing. (The sandbox
        # seeds a ``kb-shipping`` article on initialize, so we assert the
        # draft is absent rather than that the result set is empty.)
        self.assertFalse(
            any(r["id"] == article["id"] for r in self.db.search_knowledge("demo", "shipping"))
        )
        self.assertFalse(any(r["id"] == article["id"] for r in self.db.list_knowledge("demo")))
        published = self.db.review_knowledge("demo", article["id"], "publish", "admin")
        assert published is not None
        self.assertEqual(published["status"], "published")
        self.assertIn(article["id"], [r["id"] for r in self.db.list_knowledge("demo")])

    def test_publish_retired_rejected(self) -> None:
        article = self.db.create_knowledge_draft(
            "demo",
            "Title",
            "Body content",
            ["x"],
            "general",
            "https://e.com",
            "admin",
        )
        self.db.review_knowledge("demo", article["id"], "publish", "admin")
        self.db.review_knowledge("demo", article["id"], "retire", "admin")
        from app.orchestrator import InvalidTransitionError

        with self.assertRaises(InvalidTransitionError):
            self.db.review_knowledge("demo", article["id"], "publish", "admin")


# ---------------------------------------------------------------------------
# 21 golden-set regression guard for the order-no-number escalation fix
# ---------------------------------------------------------------------------


class QualityGateOrderNoNumberTests(unittest.TestCase):
    """The quality gate must escalate an order query with no order number.

    Regression guard for the ``order-no-number-escalation`` golden case: the
    OrderAgent returns a "please provide an order number" prompt with no
    ``orders.lookup`` tool call, and the QualityAgent must flag that as
    ``order_response_without_tool_record`` so the turn escalates to human.
    """

    def test_order_response_without_orders_lookup_is_flagged(self) -> None:
        from app.agents import AgentName, AgentResult, QualityAgent

        result = AgentResult(
            agent=AgentName.ORDER,
            content="请提供订单号",
            confidence=0.9,
            tool_calls=[
                {
                    "tool": "customers.resolve",
                    "success": True,
                    "code": "ok",
                    "duration_ms": 0,
                    "arguments": {"customer_ref": "CUST-1001"},
                },
            ],
        )
        assessment = QualityAgent().review(result, threshold=0.55)
        self.assertFalse(assessment.approved)
        self.assertIn("order_response_without_tool_record", assessment.issues)

    def test_crm_unavailable_handoff_still_approved(self) -> None:
        """A CRM-unavailable handoff already requires human and must pass the gate."""
        from app.agents import AgentName, AgentResult, QualityAgent

        result = AgentResult(
            agent=AgentName.ORDER,
            content="客户资料服务暂时不可用。",
            confidence=1.0,
            tool_calls=[
                {
                    "tool": "customers.resolve",
                    "success": False,
                    "code": "unavailable",
                    "duration_ms": 0,
                    "arguments": {"customer_ref": "CUST-1001"},
                },
            ],
            requires_human=True,
            handoff_reason="CRM connector unavailable",
        )
        assessment = QualityAgent().review(result, threshold=0.55)
        self.assertTrue(assessment.approved)


if __name__ == "__main__":
    unittest.main()
