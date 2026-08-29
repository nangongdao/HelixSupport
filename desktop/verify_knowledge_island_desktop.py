"""Desktop-shell real-machine verification for the knowledge island editor (D3).

Launches the release helix-desktop.exe with a WebView2 remote-debugging port,
drives the knowledge view through CDP, and asserts the island (not legacy)
renders the whole surface — including a real draft write through the
helix-knowledge-save bridge.
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
DEBUG_PORT = 9333
ISLAND_SELECTORS = {
    "island_root": "#knowledgeReactIsland .knowledge-island",
    "island_editor_aside": "#knowledgeReactIsland .knowledge-editor",
    "island_editor_form": "#knowledgeFormReact",
    "island_editor_title": "#knowledgeTitleReact",
    "island_editor_tags": "#knowledgeTagsReact",
    "legacy_editor_hidden": "#knowledgeEditor",
    "legacy_list_hidden": "#knowledgeList",
}


def wait_for_cdp(deadline_s: float = 60.0) -> list[dict]:
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
            contexts = browser.contexts
            page = None
            deadline = time.time() + 30
            while page is None and time.time() < deadline:
                for context in contexts:
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
            checks["identity"] = page.text_content("#operatorIdentity") or ""

            # Open the knowledge view. The island query already fetched on
            # mount (a view re-open only dispatches an unforced refresh), so
            # wait on the mounted island rather than a network response.
            page.locator('.nav-item[data-view="knowledge"]').click()
            page.wait_for_selector(ISLAND_SELECTORS["island_root"], timeout=15000)
            checks["island_mounted"] = True
            checks["legacy_list_hidden"] = page.evaluate(
                "() => document.querySelector('#knowledgeList').hidden === true"
                " || document.querySelector('#knowledgeList').offsetParent === null"
            )

            # Writer journey inside the island editor: open, fill, submit.
            run_id = uuid4().hex[:6]
            title = f"桌面岛编辑器验证 {run_id}"
            content = f"这是桌面壳 knowledge 岛编辑器的真机写旅程验证，运行标识 {run_id}。"
            page.wait_for_selector("#knowledgeReactIsland .knowledge-list .knowledge-action", timeout=15000)
            # The header button sits outside the island mount; in island mode
            # it opens the island editor via helix-knowledge-new.
            page.locator("#newKnowledgeDraft").click()
            page.wait_for_selector(ISLAND_SELECTORS["island_editor_form"], timeout=10000)
            checks["island_editor_opens_blank"] = page.evaluate(
                "() => document.querySelector('#knowledgeTitleReact').value === ''"
            )
            page.fill("#knowledgeTitleReact", title)
            page.fill("#knowledgeContentReact", content)
            page.fill("#knowledgeTagsReact", "desktop, island-verify")
            page.fill("#knowledgeSourceReact", "internal:desktop/verify")
            with page.expect_response(
                lambda r: "/api/knowledge/drafts" in r.url and r.request.method == "POST"
            ):
                page.locator("#knowledgeFormReact button[type='submit']").click()
            page.wait_for_timeout(1200)
            checks["draft_saved_toast"] = page.evaluate(
                "() => Boolean(document.querySelector('.toast'))"
                " || !document.querySelector('#knowledgeFormReact')"
            )
            checks["editor_closed_after_save"] = page.evaluate(
                "() => { const a = document.querySelector('#knowledgeReactIsland .knowledge-editor');"
                " return a && a.hidden === true; }"
            )
            checks["island_title_visible_after_save"] = page.evaluate(
                f"() => document.querySelector('#knowledgeReactIsland .knowledge-island')"
                f".textContent.includes({json.dumps(title)})"
            )

            print(json.dumps(checks, ensure_ascii=False, indent=2))
            failed = [k for k, v in checks.items() if v is False or v is None]
            if failed:
                print(f"FAIL: {failed}")
                return 1
            print("PASS: desktop knowledge island editor verified end-to-end")
            return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
