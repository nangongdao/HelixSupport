"""Tests for ROADMAP 18.2b knowledge-search optimizations.

Two changes are pinned here:
- ``search_knowledge`` caches FTS hit results keyed by (tenant, knowledge
  version, normalized query, language, limit): the same query returns from
  cache without re-running the FTS statement, and a knowledge write bumps the
  tenant version so a stale hit can never survive the row it was computed from.
- The no-FTS fallback scores at most ``knowledge_search_candidate_cap`` rows,
  so a very large knowledge base cannot turn one search into a full scan while
  the shared article cache (used by ``list_knowledge``) stays complete.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.database import Database


class KnowledgeSearchCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._tmp.name) / "k.db")
        self.db.initialize()
        self.db.ensure_tenant("demo")

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def _fts_query_count(self) -> int:
        with self.db._pool_lock:
            return self.db._fts_queries

    def test_repeated_query_hits_cache_without_rerunning_fts(self) -> None:
        self.db.create_knowledge(
            "demo",
            "Shipping policy",
            "Packages ship within 24 hours",
            ["shipping"],
            "general",
            "https://e.com",
        )
        first = self.db.search_knowledge("demo", "shipping")
        self.assertTrue(any("Shipping" in item["title"] for item in first))
        fts_after_first = self._fts_query_count()
        # Same normalized query ("Shipping".casefold().strip() == "shipping")
        # + language + limit → must come from cache, not rerun the FTS MATCH.
        repeated = self.db.search_knowledge("demo", "  Shipping ")
        self.assertTrue(any("Shipping" in item["title"] for item in repeated))
        cache_stats = self.db.performance_stats()["cache"]["knowledge_search"]
        self.assertGreater(cache_stats["hits"], 0)
        # No additional FTS statement was executed for the cached read.
        self.assertEqual(self._fts_query_count(), fts_after_first)

    def test_knowledge_write_invalidates_search_cache(self) -> None:
        self.db.create_knowledge(
            "demo",
            "Old policy",
            "Old shipping terms apply",
            ["shipping"],
            "general",
            "https://e.com",
        )
        self.db.search_knowledge("demo", "shipping")
        remains = self.db.search_knowledge("demo", "shipping")
        self.assertTrue(any(r["id"] == "kb-shipping" or "shipping" in r["title"] for r in remains))
        before = self.db._knowledge_version("demo")
        # A knowledge write must bump the version so the cached hit is skipped.
        self.db.create_knowledge(
            "demo",
            "New policy",
            "Newer shipping terms now apply",
            ["shipping"],
            "general",
            "https://e.com",
        )
        self.assertGreater(self.db._knowledge_version("demo"), before)
        after_write = self.db.search_knowledge("demo", "shipping")
        self.assertTrue(any("New policy" in item["title"] for item in after_write))

    def test_review_and_retire_invalidate_search_cache(self) -> None:
        article = self.db.create_knowledge_draft(
            "demo",
            "Draft",
            "Draft body shipping terms",
            ["shipping"],
            "general",
            "https://e.com",
            "admin",
        )
        published = self.db.review_knowledge("demo", article["id"], "publish", "admin")
        assert published is not None
        hits = self.db.search_knowledge("demo", "shipping")
        self.assertTrue(any(r["id"] == article["id"] for r in hits))
        before = self.db._knowledge_version("demo")
        self.db.review_knowledge("demo", article["id"], "retire", "admin")
        self.assertGreater(self.db._knowledge_version("demo"), before)
        after = self.db.search_knowledge("demo", "shipping")
        # The retired article must no longer surface even when the earlier hit
        # was cached against the pre-retire knowledge version.
        self.assertFalse(any(r["id"] == article["id"] for r in after))


class KnowledgeFallbackCandidateCapTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._tmp.name) / "k.db", knowledge_search_candidate_cap=3)
        self.db.initialize()
        self.db.ensure_tenant("demo")

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def test_fallback_only_scores_cap_candidates(self) -> None:
        # 6 articles, cap=3 → the newest 3 score; the rest are unreachable
        # through the no-FTS path. (The shared full list must still be visible
        # through list_knowledge.)
        with patch(
            "app.db.knowledge.utc_now",
            return_value="2099-08-18T00:00:00.123000+00:00",
        ):
            for index in range(6):
                self.db.create_knowledge(
                    "demo",
                    f"Article {index}",
                    f"Body of article {index}",
                    ["topic"],
                    "general",
                    "https://e.com",
                )
        listed = self.db.list_knowledge("demo")
        self.assertGreaterEqual(len(listed), 6)
        # No FTS here: force the fallback path.
        self.db._fts_enabled = False
        hits = self.db.search_knowledge("demo", "topic", limit=3)
        # With cap=3 only the three most recent articles are scored; the old
        # "Article 0" body cannot be hit.
        self.assertNotIn("Article 0", [item["title"] for item in hits])

        # A cold fallback cache must use the same deterministic newest-first
        # ordering instead of depending on the database's unspecified row
        # order.
        self.db._knowledge_cache.clear()
        cold_hits = self.db.search_knowledge("demo", "topic", limit=3)
        self.assertNotIn("Article 0", [item["title"] for item in cold_hits])


if __name__ == "__main__":
    unittest.main()
