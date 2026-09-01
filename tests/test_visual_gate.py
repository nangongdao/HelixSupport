"""Visual regression gate unit tests (scripts/visual_gate.py).

Covers the ``compare()`` verdict logic without a browser: the pixel-ratio
threshold, the bootstrap write, the explicit ``--update`` re-baseline, and the
geometry guard.

The geometry case is the reason this file exists. A size change used to return
``ok=True`` *and* overwrite the committed baseline with the new capture, so any
regression that also shifted layout height by a pixel — which most do — was
laundered into the baseline and the next run reported a clean 0.00%. These
tests pin the corrected behaviour: geometry drift fails, the baseline on disk
is left untouched, and re-baselining only happens when asked for.

Pillow is a browser-gate-only dependency (not in requirements.lock, same as
Playwright), so the suite skips when it is absent.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


def _pillow_available() -> bool:
    try:
        import PIL  # noqa: F401

        return True
    except Exception:
        return False


@unittest.skipUnless(_pillow_available(), "Requires Pillow (browser gate dependency)")
class CompareVerdictTests(unittest.TestCase):
    def setUp(self) -> None:
        import scripts.visual_gate as visual_gate

        self.visual_gate = visual_gate
        self._tmp = tempfile.TemporaryDirectory()
        self.baselines = Path(self._tmp.name)
        self._saved = visual_gate.BASELINES
        visual_gate.BASELINES = self.baselines
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        self.visual_gate.BASELINES = self._saved
        self._tmp.cleanup()

    def _image(self, size: tuple[int, int], color: tuple[int, int, int]):
        from PIL import Image

        return Image.new("RGB", size, color)

    def _baseline_pixel(self, name: str) -> tuple[int, int, int]:
        from PIL import Image

        with Image.open(self.baselines / f"{name}.png") as handle:
            return handle.convert("RGB").getpixel((0, 0))

    def test_missing_baseline_bootstraps_and_passes(self) -> None:
        ok, ratio, message = self.visual_gate.compare("boot", self._image((40, 40), (255, 0, 0)))
        self.assertTrue(ok)
        self.assertEqual(ratio, 0.0)
        self.assertIn("baseline created", message)
        self.assertTrue((self.baselines / "boot.png").is_file())

    def test_identical_capture_passes(self) -> None:
        white = self._image((40, 40), (255, 255, 255))
        self.visual_gate.compare("same", white)
        ok, ratio, _ = self.visual_gate.compare("same", white)
        self.assertTrue(ok)
        self.assertEqual(ratio, 0.0)

    def test_same_size_full_difference_fails(self) -> None:
        self.visual_gate.compare("drift", self._image((40, 40), (255, 255, 255)))
        ok, ratio, _ = self.visual_gate.compare("drift", self._image((40, 40), (0, 0, 0)))
        self.assertFalse(ok)
        self.assertEqual(ratio, 1.0)

    def test_within_tolerance_passes(self) -> None:
        """A shift under PIXEL_TOLERANCE is anti-aliasing, not a regression."""
        shade = 255 - self.visual_gate.PIXEL_TOLERANCE
        self.visual_gate.compare("aa", self._image((40, 40), (255, 255, 255)))
        ok, ratio, _ = self.visual_gate.compare("aa", self._image((40, 40), (shade, shade, shade)))
        self.assertTrue(ok)
        self.assertEqual(ratio, 0.0)

    def test_geometry_change_fails_and_preserves_baseline(self) -> None:
        """The regression this module guards: a 1px size change must not pass."""
        self.visual_gate.compare("geom", self._image((40, 40), (255, 255, 255)))
        ok, ratio, message = self.visual_gate.compare("geom", self._image((40, 41), (0, 0, 0)))
        self.assertFalse(ok)
        self.assertEqual(ratio, 1.0)
        self.assertIn("geometry changed", message)
        # The committed baseline must survive — otherwise the drift is absorbed
        # and the next run reports a clean pass.
        self.assertEqual(self._baseline_pixel("geom"), (255, 255, 255))

    def test_geometry_change_still_fails_on_rerun(self) -> None:
        """Not self-healing: the same drift fails every run until re-baselined."""
        self.visual_gate.compare("repeat", self._image((40, 40), (255, 255, 255)))
        taller = self._image((40, 41), (0, 0, 0))
        self.assertFalse(self.visual_gate.compare("repeat", taller)[0])
        self.assertFalse(self.visual_gate.compare("repeat", taller)[0])

    def test_update_rewrites_baseline(self) -> None:
        self.visual_gate.compare("intended", self._image((40, 40), (255, 255, 255)))
        ok, ratio, message = self.visual_gate.compare(
            "intended", self._image((40, 41), (0, 0, 0)), update=True
        )
        self.assertTrue(ok)
        self.assertEqual(ratio, 0.0)
        self.assertIn("baseline updated", message)
        self.assertEqual(self._baseline_pixel("intended"), (0, 0, 0))
        # And the new geometry is now the accepted one.
        self.assertTrue(self.visual_gate.compare("intended", self._image((40, 41), (0, 0, 0)))[0])


@unittest.skipUnless(_pillow_available(), "Requires Pillow (browser gate dependency)")
class UpdateFlagWiringTests(unittest.TestCase):
    """``--update`` must reach every surface, or a re-baseline half-applies."""

    def test_every_compare_call_forwards_update(self) -> None:
        import inspect

        import scripts.visual_gate as visual_gate

        source = inspect.getsource(visual_gate.main)
        calls = [line for line in source.splitlines() if "compare(" in line]
        self.assertEqual(len(calls), 4, f"expected 4 surfaces, found {len(calls)}")
        for line in calls:
            self.assertIn("update", line, f"compare() call missing update: {line.strip()}")


if __name__ == "__main__":
    unittest.main()
