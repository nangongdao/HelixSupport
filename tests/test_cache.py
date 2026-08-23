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


if __name__ == "__main__":
    unittest.main()
