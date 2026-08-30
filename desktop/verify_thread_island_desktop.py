"""Desktop-shell real-machine verification for the thread island (D3 long tail).

Launches the release helix-desktop.exe with a WebView2 remote-debugging port
and drives the message transcript end-to-end in island mode:

- selecting a conversation renders the transcript from the thread island while
  the legacy #messages stays hidden and unpainted;
- an empty conversation shows the legacy 等待第一条客户消息 empty state;
- a feedback click travels the helix-thread-feedback bridge to a real POST and
  mirrors the recorded state;
- the translate bar POSTs and degrades to the no-provider notice inline;
- sending a customer message from the composer island grows the island thread
  (legacy loadDetail republishes the snapshot);
- the 加载更早消息 affordance bridges helix-thread-load-older into the legacy
  upward keyset pagination (seed 105 messages → row count grows).
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
DEBUG_PORT = 9352
SEED_MESSAGES = 105


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
    # The sidecar inherits this env: 105 seeded turns must not trip the
    # default per-minute rate limit.
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
            page.wait_for_selector("#threadReactIsland", state="attached", timeout=30000)
            checks: dict[str, object] = {}
            checks["island_mode"] = page.evaluate("() => window.__HELIX_ISLAND_MODE__ === true")

            # Seed one conversation with a customer turn (deterministic agent
            # reply), one empty conversation, and one long conversation for
            # the upward pagination journey.
            seeded = page.evaluate(
                """async ({ messageCount }) => {
                    const headers = { 'Content-Type': 'application/json', 'X-Tenant-Id': 'demo' };
                    const post = async (url, body) => {
                        const res = await fetch(url, {
                            method: 'POST', headers,
                            body: body === undefined ? undefined : JSON.stringify(body),
                        });
                        return { ok: res.ok, status: res.status, data: res.ok ? await res.json() : null };
                    };
                    const conv = await post('/api/conversations', {
                        customer_name: '线程岛验证 ' + Math.random().toString(36).slice(2, 8),
                    });
                    if (!conv.ok) return { error: 'conversation ' + conv.status };
                    const turn = await post('/api/conversations/' + conv.data.id + '/messages', {
                        content: '配送一般多久能到？',
                    });
                    if (!turn.ok) return { error: 'turn ' + turn.status };
                    const empty = await post('/api/conversations', {
                        customer_name: '线程岛空线 ' + Math.random().toString(36).slice(2, 8),
                    });
                    if (!empty.ok) return { error: 'empty conversation ' + empty.status };
                    const long = await post('/api/conversations', {
                        customer_name: '线程岛长线 ' + Math.random().toString(36).slice(2, 8),
                    });
                    if (!long.ok) return { error: 'long conversation ' + long.status };
                    for (let i = 0; i < messageCount; i += 1) {
                        const sent = await post('/api/conversations/' + long.data.id + '/messages', {
                            content: 'lazy-message-' + String(i).padStart(4, '0'),
                        });
                        if (!sent.ok) return { error: 'seed ' + i + ': ' + sent.status };
                    }
                    return {
                        conversationId: conv.data.id,
                        emptyId: empty.data.id,
                        longId: long.data.id,
                    };
                }""",
                {"messageCount": SEED_MESSAGES},
            )
            checks["seeded"] = isinstance(seeded, dict) and "conversationId" in seeded
            if not checks["seeded"]:
                print(f"FAIL: seeding failed: {seeded}")
                return 1

            # Select the seeded conversation from the queue island.
            page.locator("#refreshList").click()
            page.wait_for_selector("#queueReactIsland .conversation-item", state="visible", timeout=30000)
            page.locator(f"#queueReactIsland .conversation-item[data-id='{seeded['conversationId']}']").click()
            page.wait_for_selector("#threadReactIsland .message-row", state="attached", timeout=15000)
            checks["island_thread_rows"] = page.evaluate(
                "() => document.querySelectorAll('#threadReactIsland .message-row').length"
            )
            checks["legacy_messages_hidden"] = page.evaluate(
                "() => { const m = document.getElementById('messages'); return m && m.hidden === true; }"
            )
            checks["legacy_messages_empty"] = page.evaluate(
                "() => document.getElementById('messages').innerHTML === ''"
            )
            checks["thread_scrolled_to_bottom"] = page.evaluate(
                """() => {
                    const el = document.getElementById('threadReactIsland');
                    return el.scrollHeight > 0 &&
                        el.scrollTop >= el.scrollHeight - el.clientHeight - 2;
                }"""
            )

            # Empty conversation → island empty state (legacy copy).
            page.locator(f"#queueReactIsland .conversation-item[data-id='{seeded['emptyId']}']").click()
            page.wait_for_function(
                "() => document.querySelector('#threadReactIsland')?.textContent.includes('等待第一条客户消息')",
                timeout=15000,
            )
            checks["island_empty_state"] = True

            # Back to the data conversation for interaction journeys.
            page.locator(f"#queueReactIsland .conversation-item[data-id='{seeded['conversationId']}']").click()
            page.wait_for_selector("#threadReactIsland .message-row", state="attached", timeout=15000)

            # ── Feedback: island click → legacy bridge → real POST ──
            with page.expect_response(
                lambda r: r.url.endswith("/feedback") and r.request.method == "POST"
            ) as feedback_info:
                page.locator("#threadReactIsland .feedback-button[data-feedback='1']").first.click()
            checks["feedback_post_status"] = feedback_info.value.status
            page.wait_for_function(
                """() => document.querySelector("#threadReactIsland .feedback-button[data-feedback='1']")
                        .classList.contains('is-recorded')""",
                timeout=10000,
            )
            checks["feedback_recorded_mirrored"] = page.evaluate(
                """() => document.querySelector("#threadReactIsland .feedback-button[data-feedback='1']")
                        .getAttribute('aria-pressed') === 'true'"""
            )

            # ── Translate: island click → legacy bridge → degrade notice ──
            with page.expect_response(
                lambda r: r.url.endswith("/translate") and r.request.method == "POST"
            ) as translate_info:
                page.locator("#threadReactIsland .translate-button").first.click()
            checks["translate_post_status"] = translate_info.value.status
            page.wait_for_function(
                """() => document.querySelector('#threadReactIsland .translate-result')?.textContent.length > 0""",
                timeout=10000,
            )
            checks["translate_result_text"] = page.evaluate(
                "() => document.querySelector('#threadReactIsland .translate-result')?.textContent"
            )

            # ── Composer island send → legacy loadDetail republishes ──
            rows_before = page.evaluate(
                "() => document.querySelectorAll('#threadReactIsland .message-row').length"
            )
            page.locator("#composerReactIsland .customer-composer textarea").fill("线程岛补发一条消息")
            page.locator("#composerReactIsland button[aria-label='发送客户消息']").click()
            page.wait_for_function(
                "(before) => document.querySelectorAll('#threadReactIsland .message-row').length > before",
                arg=rows_before,
                timeout=30000,
            )
            checks["composer_send_grew_thread"] = True

            # ── Upward pagination: island affordance → legacy keyset bridge ──
            page.locator(f"#queueReactIsland .conversation-item[data-id='{seeded['longId']}']").click()
            page.wait_for_function(
                "() => document.querySelectorAll('#threadReactIsland .message-row').length >= 100",
                timeout=20000,
            )
            rows_first_page = page.evaluate(
                "() => document.querySelectorAll('#threadReactIsland .message-row').length"
            )
            page.locator("#threadReactIsland .thread-load-older-btn").click()
            page.wait_for_function(
                "(before) => document.querySelectorAll('#threadReactIsland .message-row').length > before",
                arg=rows_first_page,
                timeout=20000,
            )
            checks["load_older_rows"] = page.evaluate(
                "() => document.querySelectorAll('#threadReactIsland .message-row').length"
            )

            print(json.dumps(checks, ensure_ascii=False, indent=2))
            failed = [k for k, v in checks.items() if v is False or v is None]
            if failed:
                print(f"FAIL: {failed}")
                return 1
            print("PASS: desktop thread island verified end-to-end")
            return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
