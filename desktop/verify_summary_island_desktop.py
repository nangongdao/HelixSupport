"""Desktop-shell real-machine verification for the summary island (D3 long tail).

The conversation summary banner (前情摘要/处置记录草稿) is island-rendered but
legacy-fed: js/renderSummaries derives the model via js/summary.js and
publishes it via helix-summary-state. The journey drives it against the real
sidecar — a fresh conversation + customer turn generates a deterministic
context summary ("自动投影"), so selecting the conversation must render the
banner from the island while the legacy #summaryBanner stays hidden, and an
empty conversation must keep the banner hidden.
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
DEBUG_PORT = 9354


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
    env.setdefault("RATE_LIMIT_PER_MINUTE", "20000")
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
            page.wait_for_selector("#summaryReactIsland", state="attached", timeout=30000)
            checks: dict[str, object] = {}
            checks["island_mode"] = page.evaluate("() => window.__HELIX_ISLAND_MODE__ === true")

            # Seed: one conversation with a customer turn (the SummaryService
            # deterministic fallback stores a context summary) and one empty
            # conversation for the hidden-banner path.
            seeded = page.evaluate(
                """async () => {
                    const headers = { 'Content-Type': 'application/json', 'X-Tenant-Id': 'demo' };
                    const post = async (url, body) => {
                        const res = await fetch(url, {
                            method: 'POST', headers,
                            body: body === undefined ? undefined : JSON.stringify(body),
                        });
                        return { ok: res.ok, status: res.status, data: res.ok ? await res.json() : null };
                    };
                    const suffix = Math.random().toString(36).slice(2, 6);
                    const conv = await post('/api/conversations', {
                        customer_name: '摘要岛验证 ' + suffix,
                    });
                    if (!conv.ok) return { error: 'conversation ' + conv.status };
                    const turn = await post('/api/conversations/' + conv.data.id + '/messages', {
                        content: '配送一般多久能到？',
                    });
                    if (!turn.ok) return { error: 'turn ' + turn.status };
                    // Context summaries generate at handoff/claim — accept so
                    // the deterministic summary exists for the banner.
                    const accept = await post('/api/conversations/' + conv.data.id + '/accept');
                    if (!accept.ok) return { error: 'accept ' + accept.status };
                    const empty = await post('/api/conversations', {
                        customer_name: '摘要岛空线 ' + suffix,
                    });
                    if (!empty.ok) return { error: 'empty conversation ' + empty.status };
                    return { conversationId: conv.data.id, emptyId: empty.data.id };
                }"""
            )
            checks["seeded"] = isinstance(seeded, dict) and "conversationId" in seeded
            if not checks["seeded"]:
                print(f"FAIL: seeding failed: {seeded}")
                return 1

            page.locator("#refreshList").click()
            page.wait_for_selector("#queueReactIsland .conversation-item", state="visible", timeout=30000)

            # Empty conversation → island banner stays hidden.
            page.locator(f"#queueReactIsland .conversation-item[data-id='{seeded['emptyId']}']").click()
            page.wait_for_function(
                """() => {
                    const banner = document.querySelector('#summaryReactIsland .summary-banner');
                    return banner && banner.hidden === true;
                }""",
                timeout=15000,
            )
            checks["hidden_on_empty_conversation"] = True

            # Data conversation → deterministic context summary renders.
            page.locator(f"#queueReactIsland .conversation-item[data-id='{seeded['conversationId']}']").click()
            page.wait_for_function(
                """() => {
                    const banner = document.querySelector('#summaryReactIsland .summary-banner');
                    return banner && banner.hidden === false && banner.textContent.includes('前情摘要');
                }""",
                timeout=20000,
            )
            checks["banner_visible_with_context_summary"] = True
            checks["banner_title"] = page.evaluate(
                "() => document.querySelector('#summaryReactIsland .csat-label span:last-child')?.textContent"
            )
            checks["banner_text"] = page.evaluate(
                "() => document.querySelector('#summaryReactIsland .summary-text')?.textContent"
            )
            checks["legacy_banner_hidden"] = page.evaluate(
                "() => { const b = document.getElementById('summaryBanner'); return b && b.hidden === true; }"
            )
            checks["legacy_banner_untouched"] = page.evaluate(
                "() => document.getElementById('summaryText').textContent === ''"
            )

            print(json.dumps(checks, ensure_ascii=False, indent=2))
            failed = [k for k, v in checks.items() if v is False or v is None]
            if failed:
                print(f"FAIL: {failed}")
                return 1
            print("PASS: desktop summary island verified end-to-end")
            return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
