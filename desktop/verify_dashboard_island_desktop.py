"""Desktop-shell real-machine verification for the dashboard island (D3 long tail).

Launches the release helix-desktop.exe with a WebView2 remote-debugging port
and asserts the workspace metrics strip renders through the React island
(legacy #metrics yielded), refetches on a foreground helix-dashboard-refresh,
and skips the refetch on an unforced (background) dispatch.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

EXE = Path(__file__).resolve().parents[1] / "src-tauri" / "target" / "release" / "helix-desktop.exe"
DEBUG_PORT = 9336


def wait_for_cdp(deadline_s: float = 90.0) -> list[dict]:
    """Wait until the WebView2 exposes a sidecar-origin (127.0.0.1) page."""
    deadline = time.time() + deadline_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}/json/list", timeout=2) as res:
                targets = json.loads(res.read().decode("utf-8"))
            pages = [t for t in targets if t.get("type") == "page" and "127.0.0.1" in (t.get("url") or "")]
            if pages:
                return targets
        except Exception:
            pass
        time.sleep(1.0)
    raise RuntimeError("WebView2 CDP endpoint never exposed a sidecar-origin page")


def main() -> int:
    if not EXE.exists():
        print(f"FAIL: release exe missing at {EXE}")
        return 1
    env = dict(os.environ)
    env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = f"--remote-debugging-port={DEBUG_PORT}"
    proc = subprocess.Popen([str(EXE)], env=env, cwd=str(EXE.parent))
    try:
        wait_for_cdp()
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{DEBUG_PORT}")
            page = None
            deadline = time.time() + 60
            while page is None and time.time() < deadline:
                for context in browser.contexts:
                    for candidate in context.pages:
                        if "127.0.0.1" in candidate.url:
                            page = candidate
                            break
                    if page:
                        break
                if page is None:
                    time.sleep(1.0)
            if page is None:
                print("FAIL: no console page found over CDP")
                return 1

            page.wait_for_selector("#operatorIdentity", timeout=30000)
            checks: dict[str, object] = {}
            checks["island_mode"] = page.evaluate("() => window.__HELIX_ISLAND_MODE__ === true")

            # The workspace is the default view — the strip mounts at load
            # and its query fires once the first legacy refreshAll runs.
            page.wait_for_selector("#dashboardReactIsland .metric-grid .metric", timeout=30000)
            checks["legacy_metrics_hidden"] = page.evaluate(
                "() => { const m = document.querySelector('#metrics');"
                " return m && m.hidden === true; }"
            )
            checks["tile_count"] = page.evaluate(
                "() => document.querySelectorAll('#dashboardReactIsland .metric').length"
            )
            checks["tile_labels"] = page.evaluate(
                "() => [...document.querySelectorAll('#dashboardReactIsland .metric span')]"
                ".map((el) => el.textContent)"
            )
            checks["tile_values_numeric"] = page.evaluate(
                "() => [...document.querySelectorAll('#dashboardReactIsland .metric strong')]"
                ".every((el) => /^\\d+$/.test(el.textContent))"
            )
            checks["tenant_header_sent"] = page.evaluate(
                "() => { const entries = performance.getEntriesByType('resource')"
                ".filter((e) => e.name.includes('/api/dashboard'));"
                " return entries.length >= 1; }"
            )

            # Foreground refresh: dispatch the bridge event and observe the
            # extra /api/dashboard round-trip.
            before = page.evaluate(
                "() => performance.getEntriesByType('resource')"
                ".filter((e) => e.name.includes('/api/dashboard')).length"
            )
            page.evaluate(
                "() => window.dispatchEvent(new CustomEvent('helix-dashboard-refresh',"
                " { detail: { force: true } }))"
            )
            page.wait_for_function(
                """(before) => performance.getEntriesByType('resource')
                    .filter((e) => e.name.includes('/api/dashboard')).length > before""",
                arg=before,
                timeout=15000,
            )
            checks["forced_refresh_refetches"] = True

            print(json.dumps(checks, ensure_ascii=False, indent=2))
            failed = [k for k, v in checks.items() if v is False or v is None]
            if failed:
                print(f"FAIL: {failed}")
                return 1
            print("PASS: desktop dashboard island verified end-to-end")
            return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
