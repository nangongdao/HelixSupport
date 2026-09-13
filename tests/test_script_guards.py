"""Guards for the historical ``app/main.py`` rewrite scripts (Phase 27.2).

``scripts/split_main.py`` and ``scripts/rebuild_main.py`` both rewrite
``app/main.py`` wholesale from the pre-split snapshot in ``app/main.py.bak``.
That snapshot is a 1.3.0-era file and both scripts carry line ranges fixed
against it, so a bare invocation silently discards everything added since the
split — including the domain-router mounts the current design depends on.

Both must therefore refuse to write without an explicit ``--apply``.

The test runs the real scripts, so a regressed guard would take ``app/main.py``
with it.  Each case snapshots the file first and restores it if the bytes
changed, so a regression surfaces as a failed assertion rather than as a
damaged working tree.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "app" / "main.py"
SCRIPTS = ("scripts/split_main.py", "scripts/rebuild_main.py")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RewriteScriptGuardTests(unittest.TestCase):
    def test_scripts_refuse_to_rewrite_main_without_apply(self) -> None:
        original = MAIN.read_bytes()
        try:
            for script in SCRIPTS:
                with self.subTest(script=script):
                    result = subprocess.run(
                        [sys.executable, script],
                        cwd=ROOT,
                        capture_output=True,
                        text=True,
                    )
                    self.assertNotEqual(result.returncode, 0, f"{script} ran without --apply")
                    self.assertIn("--apply", result.stderr, f"{script} did not name the flag")
                    if MAIN.read_bytes() != original:
                        MAIN.write_bytes(original)
                        self.fail(f"{script} rewrote app/main.py without --apply")
        finally:
            if MAIN.read_bytes() != original:
                MAIN.write_bytes(original)

    def test_guarded_scripts_still_have_their_input_and_target(self) -> None:
        """The scripts are inert, not dead — and their hazard is still live.

        The pre-split snapshot they read is still in the tree, and the file they
        would overwrite is the slimmed, router-mounting version.  That
        combination is exactly why an unguarded run would be destructive.
        """
        self.assertTrue((ROOT / "app" / "main.py.bak").exists(), "app/main.py.bak is gone")
        self.assertIn("Phase 27.2", MAIN.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
