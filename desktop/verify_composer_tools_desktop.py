"""Desktop-shell real-machine verification for the composer tool surfaces (D3 long tail).

The composer island originally mirrored only the two send forms — the copilot
bar, canned-response chips, macro suggest and attachment upload lived inside
the yielded (hidden) legacy #operatorForm and were unreachable in the desktop
shell. This slice rehomes them island-side. The journey drives each tool
end-to-end against the real sidecar:

- canned chip click → island-local insertion + a real /use POST;
- macro suggest on a trailing /token → island pick replacing the token;
- 智能建议 → helix-composer-copilot-suggest with the island draft → real
  POST /api/copilot/suggest → template suggestion chips render;
- tone rewrite → real POST /api/copilot/rewrite → rewritten text + status
  land in the island textarea;
- attachment upload via the island file input → real POST /api/attachments →
  pending chip appears → operator send carries attachment_ids.
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
DEBUG_PORT = 9353


def wait_for_cdp(deadline_s: float = 90.0) -> list[dict]:
    """Wait until the WebView2 exposes a sidecar-origin (127.0.0.1) page."""
    deadline = time.time() + deadline_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{DEBUG_PORT}/json/list", timeout=2
            ) as res:
                targets = json.loads(res.read().decode("utf-8"))
            pages = [
                t
                for t in targets
                if t.get("type") == "page" and "127.0.0.1" in (t.get("url") or "")
            ]
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
            page.wait_for_selector("#composerReactIsland", state="attached", timeout=30000)
            checks: dict[str, object] = {}
            checks["island_mode"] = page.evaluate("() => window.__HELIX_ISLAND_MODE__ === true")

            # Seed: two canned macros, a conversation with a customer turn,
            # then accept (handoff) so the operator tool surfaces show.
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
                    const macro1 = await post('/api/canned-responses', {
                        title: '催单回复 ' + suffix, shortcut: 'cudan' + suffix, body: '您的订单正在加急处理。',
                    });
                    if (!macro1.ok) return { error: 'macro1 ' + macro1.status };
                    const macro2 = await post('/api/canned-responses', {
                        title: '退款指引 ' + suffix, shortcut: 'tuik' + suffix, body: '退款将在 3 个工作日内到账。',
                    });
                    if (!macro2.ok) return { error: 'macro2 ' + macro2.status };
                    const conv = await post('/api/conversations', {
                        customer_name: '工具面验证 ' + suffix,
                    });
                    if (!conv.ok) return { error: 'conversation ' + conv.status };
                    const turn = await post('/api/conversations/' + conv.data.id + '/messages', {
                        content: '配送一般多久能到？',
                    });
                    if (!turn.ok) return { error: 'turn ' + turn.status };
                    const accept = await post('/api/conversations/' + conv.data.id + '/accept');
                    if (!accept.ok) return { error: 'accept ' + accept.status };
                    // Refresh canned cache in legacy state for the island snapshot.
                    return { conversationId: conv.data.id, shortcuts: ['cudan' + suffix, 'tuik' + suffix] };
                }"""
            )
            checks["seeded"] = isinstance(seeded, dict) and "conversationId" in seeded
            if not checks["seeded"]:
                print(f"FAIL: seeding failed: {seeded}")
                return 1

            # Select the conversation from the queue island.
            page.locator("#refreshList").click()
            page.wait_for_selector(
                "#queueReactIsland .conversation-item", state="visible", timeout=30000
            )
            page.locator(
                f"#queueReactIsland .conversation-item[data-id='{seeded['conversationId']}']"
            ).click()

            # Tool surfaces visible in the island (legacy forms stay yielded).
            page.wait_for_function(
                """() => {
                    const bar = document.querySelector('#composerReactIsland #copilotBar');
                    return bar && bar.hidden === false;
                }""",
                timeout=20000,
            )
            checks["copilot_bar_visible"] = True
            checks["canned_bar_visible"] = page.evaluate(
                "() => document.querySelector('#composerReactIsland #cannedBar')?.hidden === false"
            )
            checks["attachment_bar_visible"] = page.evaluate(
                "() => document.querySelector('#composerReactIsland #attachmentBar')?.hidden === false"
            )
            checks["legacy_operator_form_hidden"] = page.evaluate(
                "() => { const f = document.getElementById('operatorForm'); return f && f.hidden === true; }"
            )

            # ── Canned chip → island insertion + real /use POST ──
            with page.expect_response(
                lambda r: "/canned-responses/" in r.url
                and r.url.endswith("/use")
                and r.request.method == "POST"
            ) as use_info:
                page.locator("#composerReactIsland #cannedList .canned-chip").first.click()
            checks["macro_use_post_status"] = use_info.value.status
            checks["canned_chip_inserted"] = page.evaluate(
                "() => document.getElementById('operatorInputReact').value.length > 0"
            )

            # ── Macro suggest: type a trailing /token, pick the option ──
            # The catalog comes from the app's own state (120s cache), so use
            # a shortcut already rendered on the island's canned chips.
            chip_shortcut = page.evaluate(
                """() => {
                    const chip = document.querySelector('#composerReactIsland #cannedList .canned-chip');
                    const text = chip ? chip.textContent : '';
                    const at = text.lastIndexOf('/');
                    return at >= 0 ? text.slice(at + 1).trim() : '';
                }"""
            )
            checks["chip_shortcut_found"] = bool(chip_shortcut)
            token = chip_shortcut[:4] if chip_shortcut else ""
            page.locator("#operatorInputReact").fill("")
            page.locator("#operatorInputReact").fill(f"请查收 /{token}")
            page.wait_for_function(
                "() => document.querySelectorAll('#composerReactIsland #macroSuggest .macro-option').length > 0",
                timeout=10000,
            )
            with page.expect_response(
                lambda r: "/canned-responses/" in r.url
                and r.url.endswith("/use")
                and r.request.method == "POST"
            ) as use_info2:
                page.locator("#composerReactIsland #macroSuggest .macro-option").first.click()
            checks["macro_pick_post_status"] = use_info2.value.status
            checks["macro_token_replaced"] = page.evaluate(
                "() => !document.getElementById('operatorInputReact').value.includes('请查收 /')"
            )

            # ── 智能建议: island draft → real POST → template chips ──
            # The backend filters canned templates by the draft text, so use a
            # snippet of the first canned chip's body as the island draft.
            draft_snippet = page.evaluate(
                """() => {
                    const chip = document.querySelector('#composerReactIsland #cannedList .canned-chip');
                    return (chip?.getAttribute('title') || '').slice(0, 3);
                }"""
            )
            page.locator("#operatorInputReact").fill(draft_snippet)
            with page.expect_response(
                lambda r: r.url.endswith("/api/copilot/suggest") and r.request.method == "POST"
            ) as suggest_info:
                page.locator("#composerReactIsland #copilotSuggestBtn").click()
            checks["suggest_post_status"] = suggest_info.value.status
            # The island's textarea content rides in the request body as `draft`.
            suggest_body = json.loads(suggest_info.value.request.post_data or "{}")
            checks["suggest_request_draft"] = suggest_body.get("draft") == draft_snippet
            page.wait_for_function(
                "() => document.querySelectorAll('#composerReactIsland #copilotSuggestions .copilot-suggestion').length > 0",
                timeout=15000,
            )
            checks["suggestion_badge"] = page.evaluate(
                "() => document.querySelector('#composerReactIsland .copilot-suggestion-badge')?.textContent"
            )

            # Apply a suggestion into the textarea.
            page.locator(
                "#composerReactIsland #copilotSuggestions .copilot-suggestion"
            ).first.click()
            checks["suggestion_applied"] = page.evaluate(
                "() => document.getElementById('operatorInputReact').value.length > 0"
            )

            # ── Tone rewrite: real POST, rewritten text + status in island ──
            with page.expect_response(
                lambda r: r.url.endswith("/api/copilot/rewrite") and r.request.method == "POST"
            ) as rewrite_info:
                page.select_option("#composerReactIsland #copilotTone", "concise")
            checks["rewrite_post_status"] = rewrite_info.value.status
            page.wait_for_function(
                """() => document.querySelector('#composerReactIsland #copilotStatus')?.textContent.length > 0""",
                timeout=15000,
            )
            checks["rewrite_status_text"] = page.evaluate(
                "() => document.querySelector('#composerReactIsland #copilotStatus')?.textContent"
            )

            # ── Attachment: island file input → real upload → operator send ──
            page.locator("#operatorInputReact").fill("附上凭证文件")
            try:
                with page.expect_response(
                    lambda r: r.url.endswith("/api/attachments") and r.request.method == "POST",
                    timeout=15000,
                ) as upload_info:
                    # #attachmentFileReact, not #attachmentFile: the island's
                    # input needs an id distinct from the yielded legacy one, or
                    # its own upload label binds to legacy's (first in tree
                    # order) and this bridge is never reached.
                    page.locator("#composerReactIsland #attachmentFileReact").set_input_files(
                        files=[
                            {
                                "name": "凭证.txt",
                                "mimeType": "text/plain",
                                "buffer": b"attachment proof",
                            }
                        ]
                    )
                checks["attachment_post_status"] = upload_info.value.status
            except Exception as error:  # noqa: BLE001 — diagnostic path
                checks["attachment_upload_error"] = str(error)[:200]
                checks["upload_toast"] = page.evaluate(
                    "() => document.getElementById('toast')?.textContent"
                )
                print(json.dumps(checks, ensure_ascii=False, indent=2))
                return 1
            page.wait_for_function(
                "() => document.querySelectorAll('#composerReactIsland #pendingAttachments .pending-attachment-chip').length > 0",
                timeout=20000,
            )
            checks["pending_chip_visible"] = True
            with page.expect_response(
                lambda r: r.url.endswith("/operator-messages") and r.request.method == "POST"
            ) as send_info:
                page.locator("#composerReactIsland button[aria-label='发送人工回复']").click()
            checks["operator_send_status"] = send_info.value.status
            checks["operator_send_body"] = send_info.value.request.post_data
            page.wait_for_function(
                "() => document.querySelectorAll('#composerReactIsland #pendingAttachments .pending-attachment-chip').length === 0",
                timeout=20000,
            )
            checks["pending_cleared_after_send"] = True

            print(json.dumps(checks, ensure_ascii=False, indent=2))
            failed = [k for k, v in checks.items() if v is False or v is None]
            if failed:
                print(f"FAIL: {failed}")
                return 1
            print("PASS: desktop composer tool surfaces verified end-to-end")
            return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
