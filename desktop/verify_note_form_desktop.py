"""Desktop-shell real-machine verification for the island-owned note composer (D3 long tail).

The internal-note form (previously a legacy form inside the inspector aside)
now renders from the inspector island with the legacy DOM contract (#noteForm/
#noteInput/#mentionSuggest), while the write + completion feedback stay legacy
(submitNote) so api()/toast/loadDetail remain in one place. The journey drives
it against the real sidecar:

- selecting a not-resolved conversation shows the island note form while the
  legacy #noteForm stays yielded (hidden);
- submitting a note POSTs to /api/conversations/{id}/notes, toasts and clears
  the island textarea (helix-inspector-note-submitted ok echo);
- typing a trailing @token renders mention candidates from the tenant roster
  (self excluded), and clicking one replaces the token at the caret;
- keyboard navigation (ArrowDown + Enter) applies the active candidate.
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
DEBUG_PORT = 9355


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
            page.wait_for_selector("#inspectorReactIsland", state="attached", timeout=30000)
            checks: dict[str, object] = {}
            checks["island_mode"] = page.evaluate("() => window.__HELIX_ISLAND_MODE__ === true")

            # Seed: a colleague for the mention roster + a conversation with a
            # customer turn (not resolved, so the note form shows).
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
                    const member = await post('/api/admin/tenants/demo/members', {
                        actor_id: 'supervisor.b', role: 'operator',
                    });
                    if (!member.ok && member.status !== 200) return { error: 'member ' + member.status };
                    const conv = await post('/api/conversations', {
                        customer_name: '备注岛验证 ' + Math.random().toString(36).slice(2, 8),
                    });
                    if (!conv.ok) return { error: 'conversation ' + conv.status };
                    const turn = await post('/api/conversations/' + conv.data.id + '/messages', {
                        content: '配送一般多久能到？',
                    });
                    if (!turn.ok) return { error: 'turn ' + turn.status };
                    return { conversationId: conv.data.id };
                }"""
            )
            checks["seeded"] = isinstance(seeded, dict) and "conversationId" in seeded
            if not checks["seeded"]:
                print(f"FAIL: seeding failed: {seeded}")
                return 1

            page.locator("#refreshList").click()
            page.wait_for_selector("#queueReactIsland .conversation-item", state="visible", timeout=30000)
            page.locator(f"#queueReactIsland .conversation-item[data-id='{seeded['conversationId']}']").click()

            # Island note form visible; legacy note form stays yielded.
            page.wait_for_function(
                """() => {
                    const form = document.querySelector('#inspectorReactIsland #noteForm');
                    return form && form.hidden === false;
                }""",
                timeout=20000,
            )
            checks["island_note_form_visible"] = True
            checks["legacy_note_form_hidden"] = page.evaluate(
                "() => { const forms = [...document.querySelectorAll('#noteForm')];"
                " const legacy = forms[forms.length - 1];"
                " return forms.length === 2 && legacy.hidden === true; }"
            )

            # ── Note submit: real POST, toast, island textarea cleared ──
            with page.expect_response(
                lambda r: r.url.endswith("/notes") and r.request.method == "POST"
            ) as note_info:
                page.locator("#inspectorReactIsland #noteInput").fill("CDP 验证：内部备注已记录")
                page.locator("#inspectorReactIsland #noteForm button[type='submit']").click()
            checks["note_post_status"] = note_info.value.status
            page.wait_for_function(
                """() => document.querySelector('#inspectorReactIsland #noteInput').value === ''""",
                timeout=15000,
            )
            checks["note_cleared_on_ok"] = True
            checks["note_toast_visible"] = page.evaluate(
                "() => document.getElementById('toast')?.textContent?.includes('内部备注已添加')"
            )

            # ── Mention suggest: trailing @token from the roster ──
            page.locator("#inspectorReactIsland #noteInput").fill("麻烦 @su")
            page.locator("#inspectorReactIsland #noteInput").press("End")
            page.wait_for_function(
                "() => document.querySelectorAll('#inspectorReactIsland #mentionSuggest .macro-option').length > 0",
                timeout=15000,
            )
            checks["mention_option_text"] = page.evaluate(
                "() => document.querySelector('#inspectorReactIsland #mentionSuggest .macro-option strong')?.textContent"
            )
            page.locator("#inspectorReactIsland #mentionSuggest .macro-option").first.click()
            checks["mention_applied"] = page.evaluate(
                "() => document.querySelector('#inspectorReactIsland #noteInput').value.includes('@supervisor.b ')"
            )

            # ── Keyboard navigation: ArrowDown + Enter applies the active ──
            page.locator("#inspectorReactIsland #noteInput").fill("再 @su")
            page.locator("#inspectorReactIsland #noteInput").press("End")
            page.wait_for_function(
                "() => document.querySelectorAll('#inspectorReactIsland #mentionSuggest .macro-option').length > 0",
                timeout=15000,
            )
            page.locator("#inspectorReactIsland #noteInput").press("ArrowDown")
            page.locator("#inspectorReactIsland #noteInput").press("Enter")
            page.wait_for_function(
                "() => document.querySelector('#inspectorReactIsland #noteInput').value.includes('@supervisor.b ')",
                timeout=15000,
            )
            checks["keyboard_mention_applied"] = True

            print(json.dumps(checks, ensure_ascii=False, indent=2))
            failed = [k for k, v in checks.items() if v is False or v is None]
            if failed:
                print(f"FAIL: {failed}")
                return 1
            print("PASS: desktop note composer verified end-to-end")
            return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
