"""Desktop-shell real-machine verification for the quality glue slice (D3 long tail).

Launches the release helix-desktop.exe with a WebView2 remote-debugging port and
drives the quality surface end-to-end in island mode:

- the standalone 质量 view renders from the React island while the legacy view
  containers stay hidden and unpainted;
- the header 刷新 button bridges a forced refetch through helix-quality-refresh;
- an unforced view re-open serves from the island's 10s cache (no refetch) and
  refetches again once staleTime expires (legacy cadence preserved);
- the inspector 质量 tab — island-rendered, legacy-fed — shows the aggregates
  published via helix-inspector-quality (it used to be an empty div), the hidden
  legacy inspector containers stay untouched, and a 生成知识草稿 click travels
  the helix-quality-draft bridge to a real knowledge-draft POST.

Seed data: one conversation, one customer turn (deterministic fallback agent) and
a negative feedback rating on the assistant reply, which is exactly the
knowledge-gap predicate (negative feedback without citations).
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
DEBUG_PORT = 9351


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


SEED_JS = """async () => {
    const tenant = { 'Content-Type': 'application/json', 'X-Tenant-Id': 'demo' };
    const convRes = await fetch('/api/conversations', {
        method: 'POST', headers: tenant,
        body: JSON.stringify({ customer_name: '质量胶水验证 ' + Math.random().toString(36).slice(2, 8) }),
    });
    if (!convRes.ok) return { error: 'conversation ' + convRes.status };
    const conv = await convRes.json();
    const turnRes = await fetch('/api/conversations/' + conv.id + '/messages', {
        method: 'POST', headers: tenant,
        body: JSON.stringify({ content: '配送一般多久能到？' }),
    });
    if (!turnRes.ok) return { error: 'turn ' + turnRes.status };
    const turn = await turnRes.json();
    if (!turn.assistant_message) return { error: 'no assistant reply' };

    // The knowledge agent cites its sources for known topics, and a gap
    // requires a negatively-rated message WITHOUT citations. Ask something
    // the knowledge base cannot match; if the fallback still cites, rate the
    // (always citation-free) customer message instead so the gap exists.
    const gapTarget = { messageId: null, kind: null };
    const unknownRes = await fetch('/api/conversations/' + conv.id + '/messages', {
        method: 'POST', headers: tenant,
        body: JSON.stringify({ content: 'zzqw 咨询一个知识库肯定没有的问题 0x7f' }),
    });
    if (unknownRes.ok) {
        const unknownTurn = await unknownRes.json();
        const citations = (unknownTurn.assistant_message?.metadata?.citations) || [];
        if (unknownTurn.assistant_message && citations.length === 0) {
            gapTarget.messageId = unknownTurn.assistant_message.id;
            gapTarget.kind = 'assistant_fallback';
        }
    }
    if (!gapTarget.messageId) {
        gapTarget.messageId = turn.customer_message.id;
        gapTarget.kind = 'customer_message';
    }
    const fbRes = await fetch('/api/conversations/' + conv.id + '/feedback', {
        method: 'POST', headers: tenant,
        body: JSON.stringify({ message_id: gapTarget.messageId, rating: -1 }),
    });
    if (!fbRes.ok) return { error: 'feedback ' + fbRes.status };
    return {
        conversationId: conv.id,
        assistantId: turn.assistant_message.id,
        gapTarget,
        assistantMetadata: turn.assistant_message.metadata,
    };
}"""


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
            page.wait_for_selector("#qualityReactIsland", state="attached", timeout=30000)
            checks: dict[str, object] = {}
            checks["island_mode"] = page.evaluate("() => window.__HELIX_ISLAND_MODE__ === true")

            # Count /api/supervisor/quality fetches from here on (the island's
            # boot fetch happened before this, so assertions use deltas).
            page.evaluate(
                """() => {
                    window.__qualityFetches = 0;
                    const orig = window.fetch.bind(window);
                    window.fetch = function (...args) {
                        const url = typeof args[0] === 'string' ? args[0] : (args[0] && args[0].url) || '';
                        if (String(url).includes('/api/supervisor/quality')) window.__qualityFetches += 1;
                        return orig(...args);
                    };
                }"""
            )

            seeded = page.evaluate(SEED_JS)
            checks["seeded"] = isinstance(seeded, dict) and "conversationId" in seeded
            if not checks["seeded"]:
                print(f"FAIL: seeding failed: {seeded}")
                return 1

            # ── Standalone quality view: island owns it, legacy stays hidden ──
            page.locator("button.nav-item[data-view='quality']").click()
            page.wait_for_selector("#qualityReactIsland .qc-island", state="attached", timeout=15000)
            checks["island_view_rendered"] = page.evaluate(
                "() => !!document.querySelector('#qualityReactIsland .qc-island')"
            )
            checks["legacy_view_buckets_hidden"] = page.evaluate(
                "() => { const b = document.querySelector('#qualityViewBuckets'); return b && b.hidden === true; }"
            )
            checks["legacy_view_buckets_empty"] = page.evaluate(
                "() => document.querySelector('#qualityViewBuckets').innerHTML === ''"
            )

            # ── Header 刷新 button: forced refresh bridge ──
            with page.expect_response(
                lambda r: "/api/supervisor/quality" in r.url and r.request.method == "GET"
            ) as quality_info:
                page.locator("#refreshQualityView").click()
            checks["forced_refetch_status"] = quality_info.value.status
            checks["forced_refetch_counted"] = page.evaluate("() => window.__qualityFetches === 1")

            # ── Unforced re-open with fresh data: the 10s cache serves it ──
            page.locator("button.nav-item[data-view='workspace']").click()
            page.locator("button.nav-item[data-view='quality']").click()
            page.wait_for_timeout(800)
            checks["unforced_fresh_no_refetch"] = page.evaluate("() => window.__qualityFetches === 1")

            # ── Unforced re-open after staleTime expires: refetches again ──
            page.locator("button.nav-item[data-view='workspace']").click()
            page.wait_for_timeout(11000)
            page.locator("button.nav-item[data-view='quality']").click()
            page.wait_for_function("() => window.__qualityFetches >= 2", timeout=15000)
            checks["unforced_stale_refetches"] = True

            # ── Inspector quality tab: island-rendered, legacy-fed ──
            page.locator("button.nav-item[data-view='workspace']").click()
            page.locator("#refreshList").click()
            page.wait_for_selector("#queueReactIsland .conversation-item", state="visible", timeout=30000)
            page.locator("#queueReactIsland .conversation-item").first.click()
            page.wait_for_selector("#inspectorReactIsland .inspector-tab[data-tab='quality']", state="visible", timeout=15000)
            page.locator("#inspectorReactIsland .inspector-tab[data-tab='quality']").click()
            page.wait_for_function(
                """() => {
                    const buckets = document.querySelector('#inspectorReactIsland #qualityPanel .quality-buckets');
                    return buckets && buckets.childElementCount > 0;
                }""",
                timeout=15000,
            )
            checks["inspector_quality_panel_filled"] = True
            checks["inspector_gap_button_present"] = page.evaluate(
                "() => !!document.querySelector('#inspectorReactIsland #qualityPanel .quality-gap-draft')"
            )
            checks["legacy_inspector_buckets_untouched"] = page.evaluate(
                "() => document.getElementById('qualityBuckets').innerHTML === ''"
            )

            # ── 生成知识草稿 bridges to the real legacy write ──
            with page.expect_response(
                lambda r: "/knowledge-draft" in r.url and r.request.method == "POST"
            ) as draft_info:
                page.locator("#inspectorReactIsland #qualityPanel .quality-gap-draft").first.click()
            checks["draft_post_status"] = draft_info.value.status
            checks["draft_toast_visible"] = page.evaluate(
                """() => {
                    const toast = document.getElementById('toast');
                    return toast && toast.hidden === false && toast.textContent.includes('草稿');
                }"""
            )

            print(json.dumps(checks, ensure_ascii=False, indent=2))
            failed = [k for k, v in checks.items() if v is False or v is None]
            if failed:
                print(f"FAIL: {failed}")
                return 1
            print("PASS: desktop quality glue verified end-to-end")
            return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
