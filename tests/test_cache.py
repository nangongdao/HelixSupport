"""TTLCache — bounded per-process cache for tenant-scoped data.

Direct unit coverage of the small cache layer: constructor validation,
hit/miss accounting, expiry (including the stale-entry removal path),
LRU eviction beyond max_entries, invalidate / clear, and stats.
"""

from __future__ import annotations

import time
import unittest

from app.cache import TTLCache


class TTLCacheTests(unittest.TestCase):
    def test_hit_miss_invalidate_and_clear(self) -> None:
        cache: TTLCache[str, int] = TTLCache(10, max_entries=2)
        self.assertIsNone(cache.get("missing"))
        cache.set("a", 1)
        self.assertEqual(cache.get("a"), 1)
        cache.invalidate("a")
        self.assertIsNone(cache.get("a"))
        cache.set("b", 2)
        cache.clear()
        self.assertIsNone(cache.get("b"))

    def test_expiration_and_lru_eviction_are_reported(self) -> None:
        cache: TTLCache[str, int] = TTLCache(0.01, max_entries=1)
        cache.set("a", 1)
        time.sleep(0.02)
        self.assertIsNone(cache.get("a"))
        cache.set("a", 1)
        cache.set("b", 2)
        self.assertIsNone(cache.get("a"))
        self.assertEqual(cache.get("b"), 2)
        stats = cache.stats()
        self.assertGreaterEqual(stats.misses, 2)
        self.assertEqual(stats.evictions, 1)
        self.assertEqual(stats.entries, 1)

    def test_invalid_configuration_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            TTLCache(0)
        with self.assertRaises(ValueError):
            TTLCache(1, max_entries=0)

    def test_constructor_rejects_non_positive_ttl(self) -> None:
        with self.assertRaisesRegex(ValueError, "ttl_seconds"):
            TTLCache(ttl_seconds=0)
        with self.assertRaisesRegex(ValueError, "ttl_seconds"):
            TTLCache(ttl_seconds=-1)

    def test_constructor_rejects_zero_max_entries(self) -> None:
        with self.assertRaisesRegex(ValueError, "max_entries"):
            TTLCache(ttl_seconds=10, max_entries=0)

    def test_get_miss_counts_and_returns_none(self) -> None:
        cache: TTLCache[str, int] = TTLCache(ttl_seconds=10)
        self.assertIsNone(cache.get("missing"))
        self.assertEqual(cache.stats().misses, 1)

    def test_set_get_hit_counts(self) -> None:
        cache: TTLCache[str, int] = TTLCache(ttl_seconds=10)
        cache.set("k", 42)
        self.assertEqual(cache.get("k"), 42)
        self.assertEqual(cache.stats().hits, 1)
        self.assertEqual(cache.stats().entries, 1)

    def test_expired_entry_is_removed_and_misses(self) -> None:
        cache: TTLCache[str, int] = TTLCache(ttl_seconds=0.05)
        cache.set("k", 42)
        time.sleep(0.1)
        self.assertIsNone(cache.get("k"))
        self.assertEqual(cache.stats().misses, 1)
        self.assertEqual(cache.stats().entries, 0)

    def test_lru_eviction_beyond_max_entries(self) -> None:
        cache: TTLCache[int, int] = TTLCache(ttl_seconds=10, max_entries=2)
        cache.set(1, "a")
        cache.set(2, "b")
        cache.set(3, "c")
        self.assertEqual(cache.stats().evictions, 1)
        self.assertIsNone(cache.get(1))
        self.assertEqual(cache.get(2), "b")
        self.assertEqual(cache.get(3), "c")

    def test_invalidate_removes_entry(self) -> None:
        cache: TTLCache[str, int] = TTLCache(ttl_seconds=10)
        cache.set("k", 42)
        cache.invalidate("k")
        self.assertIsNone(cache.get("k"))
        self.assertEqual(cache.stats().entries, 0)

    def test_invalidate_missing_key_is_noop(self) -> None:
        cache: TTLCache[str, int] = TTLCache(ttl_seconds=10)
        cache.invalidate("missing")  # must not raise

    def test_clear_empties_cache(self) -> None:
        cache: TTLCache[str, int] = TTLCache(ttl_seconds=10)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.clear()
        self.assertEqual(cache.stats().entries, 0)
        self.assertIsNone(cache.get("a"))

    def test_stats_reflect_activity(self) -> None:
        cache: TTLCache[str, int] = TTLCache(ttl_seconds=10, max_entries=2)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.get("a")  # hit
        cache.get("missing")  # miss
        stats = cache.stats()
        self.assertEqual(stats.hits, 1)
        self.assertEqual(stats.misses, 1)
        self.assertEqual(stats.evictions, 0)
        self.assertEqual(stats.entries, 2)

    def test_stats_eviction_after_overfill(self) -> None:
        cache: TTLCache[str, int] = TTLCache(ttl_seconds=10, max_entries=1)
        cache.set("a", 1)
        cache.set("b", 2)  # max_entries=1: "a" evicted
        stats = cache.stats()
        self.assertEqual(stats.evictions, 1)
        self.assertEqual(stats.entries, 1)
        self.assertIsNone(cache.get("a"))


if __name__ == "__main__":
    unittest.main()
