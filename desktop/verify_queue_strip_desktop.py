"""Desktop-shell real-machine verification for the queue island strip (D3 long tail).

Launches the release helix-desktop.exe with a WebView2 remote-debugging port,
seeds enough conversations to overflow the first queue page, and drives the
island's footer strip end-to-end: count with the "+" suffix, the 加载更多
button bridging to legacy loadMoreConversations (a cursor request), and the
legacy #queueCount/#loadMore staying yielded.
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
DEBUG_PORT = 9337
SEED_COUNT = 60  # QUEUE_PAGE_SIZE_NORMAL is 50 — 60 forces a second page


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
            checks["legacy_queue_count_hidden"] = page.evaluate(
                "() => { const q = document.querySelector('#queueCount');"
                " return q && q.hidden === true; }"
            )
            checks["island_strip_rendered"] = page.evaluate(
                "() => Boolean(document.querySelector('#queueReactIsland .queue-footer'))"
            )
            initial_count = page.evaluate(
                "() => document.querySelector('#queueReactIsland .queue-footer span')?.textContent || ''"
            )
            checks["initial_count_format"] = initial_count.endswith("个会话")

            # Seed enough conversations to overflow the first page (50 rows).
            uuid4().hex[:6]
            seeded = page.evaluate(
                """async (count) => {
                    let ok = 0;
                    for (let i = 0; i < count; i += 1) {
                        const res = await fetch('/api/conversations', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json', 'X-Tenant-Id': 'demo' },
                            body: JSON.stringify({
                                customer_name: `分页验证 ${i} (${Math.random().toString(36).slice(2, 8)})`,
                                channel: 'web',
                            }),
                        });
                        if (res.ok) ok += 1;
                    }
                    return ok;
                }""",
                SEED_COUNT,
            )
            checks["seeded_conversations"] = seeded

            # A foreground refresh (header button) picks up the new rows.
            page.locator("#refreshList").click()
            page.wait_for_function(
                "() => /\\d+\\+ 个会话/.test("
                "document.querySelector('#queueReactIsland .queue-footer span')?.textContent || '')",
                timeout=30000,
            )
            strip_text = page.evaluate(
                "() => document.querySelector('#queueReactIsland .queue-footer span')?.textContent"
            )
            checks["count_has_plus_suffix"] = True
            checks["strip_count_text"] = strip_text
            checks["legacy_load_more_hidden"] = page.evaluate(
                "() => { const b = document.querySelector('#loadMore');"
                " return b && b.hidden === true; }"
            )
            rows_before = page.evaluate(
                "() => document.querySelectorAll('#queueReactIsland .conversation-item').length"
            )

            # The island's 加载更多 bridges to legacy loadMoreConversations —
            # a real cursor request for the second page.
            with page.expect_response(
                lambda r: "/api/conversations" in r.url and "cursor=" in r.url
            ):
                page.locator("#queueReactIsland .queue-more").click()
            page.wait_for_function(
                """(before) => document.querySelectorAll(
                       '#queueReactIsland .conversation-item').length > before""",
                arg=rows_before,
                timeout=30000,
            )
            checks["load_more_cursor_request"] = True
            checks["rows_after_load_more"] = page.evaluate(
                "() => document.querySelectorAll('#queueReactIsland .conversation-item').length"
            )

            print(json.dumps(checks, ensure_ascii=False, indent=2))
            failed = [k for k, v in checks.items() if v is False or v is None]
            if failed:
                print(f"FAIL: {failed}")
                return 1
            print("PASS: desktop queue strip verified end-to-end")
            return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
