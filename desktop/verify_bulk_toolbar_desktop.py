"""Desktop-shell real-machine verification for the queue island bulk toolbar (D3 long tail).

Launches the release helix-desktop.exe with a WebView2 remote-debugging port,
selects two island queue rows, and drives the island bulk toolbar end-to-end:
count readout, a priority bulk action bridging to legacy (a real bulk-actions
POST), the toolbar clearing after success, and the legacy #bulkToolbar
staying yielded.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import sync_playwright

EXE = Path(__file__).resolve().parents[1] / "src-tauri" / "target" / "release" / "helix-desktop.exe"
DEBUG_PORT = 9340
SEED_COUNT = 3


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

            page.wait_for_selector("#operatorIdentity", state="attached", timeout=30000)
            page.wait_for_selector("#queueReactIsland .conversation-item", state="attached", timeout=30000)
            checks: dict[str, object] = {}
            checks["island_mode"] = page.evaluate("() => window.__HELIX_ISLAND_MODE__ === true")
            checks["legacy_bulk_toolbar_hidden"] = page.evaluate(
                "() => { const b = document.querySelector('#bulkToolbar');"
                " return b && b.hidden === true; }"
            )
            checks["toolbar_absent_without_selection"] = page.evaluate(
                "() => !document.querySelector('#queueReactIsland .bulk-toolbar')"
            )

            # Seed conversations so at least two island rows exist.
            run_id = uuid4().hex[:6]
            seeded = page.evaluate(
                """async (count) => {
                    let ok = 0;
                    for (let i = 0; i < count; i += 1) {
                        const res = await fetch('/api/conversations', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json', 'X-Tenant-Id': 'demo' },
                            body: JSON.stringify({ customer_name: `批量验证 ${i} (${Math.random().toString(36).slice(2, 8)})` }),
                        });
                        if (res.ok) ok += 1;
                    }
                    return ok;
                }""",
                SEED_COUNT,
            )
            checks["seeded"] = seeded
            page.locator("#refreshList").click()
            page.wait_for_function(
                "() => document.querySelectorAll('#queueReactIsland .conversation-item').length >= 3",
                timeout=30000,
            )

            # Select two island rows via their bulk checkboxes.
            checkboxes = page.locator("#queueReactIsland .conversation-checkbox")
            checkboxes.nth(0).check()
            checkboxes.nth(1).check()
            page.wait_for_selector("#queueReactIsland .bulk-toolbar", timeout=10000)
            checks["toolbar_count"] = page.evaluate(
                "() => document.querySelector('#queueReactIsland .bulk-count')?.textContent"
            )

            # Apply a priority action through the bridge — a real POST.
            page.locator("#queueReactIsland .bulk-action-field select").select_option("priority-high")
            with page.expect_response(
                lambda r: r.url.endswith("/api/conversations/bulk-actions")
                and r.request.method == "POST"
            ) as bulk_info:
                page.locator("#queueReactIsland .bulk-icon-button.is-primary").click()
            checks["bulk_post_status"] = bulk_info.value.status
            checks["bulk_updated"] = bulk_info.value.json().get("updated")

            # Success clears the selection -> toolbar disappears again.
            page.wait_for_function(
                "() => !document.querySelector('#queueReactIsland .bulk-toolbar')",
                timeout=15000,
            )
            checks["toolbar_cleared_after_success"] = True

            print(json.dumps(checks, ensure_ascii=False, indent=2))
            failed = [k for k, v in checks.items() if v is False or v is None]
            if failed:
                print(f"FAIL: {failed}")
                return 1
            print("PASS: desktop bulk toolbar verified end-to-end")
            return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
