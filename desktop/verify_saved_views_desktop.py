"""Desktop-shell real-machine verification for the saved views island (D3 long tail).

Launches the release helix-desktop.exe with a WebView2 remote-debugging port
and drives the saved-views controls end-to-end: save (prompt -> POST ->
island reselects), apply (island selection rewrites the legacy filter
inputs and fires a fresh queue request), delete (DELETE -> selection
drops), with the legacy controls yielded throughout.
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
DEBUG_PORT = 9342


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
            checks["legacy_field_hidden"] = page.evaluate(
                "() => { const l = document.querySelector('#savedViewField');"
                " return l && l.hidden === true; }"
            )
            checks["island_controls_present"] = page.evaluate(
                "() => Boolean(document.querySelector('#savedViewSelectReact')"
                " && document.querySelector('#saveViewReact')"
                " && document.querySelector('#deleteViewReact'))"
            )
            checks["delete_disabled_without_selection"] = page.evaluate(
                "() => document.querySelector('#deleteViewReact')?.disabled === true"
            )

            # Save journey: the island prompts, legacy POSTs with the current
            # filter inputs, the island refetches and reselects the view.
            run_id = uuid4().hex[:6]
            view_name = f"保存视图验证 {run_id}"
            prompts: list[str] = []
            page.on(
                "dialog",
                lambda dialog: (prompts.append(dialog.message), dialog.accept(view_name)),
            )
            with page.expect_response(
                lambda r: r.url.endswith("/api/saved-views") and r.request.method == "POST"
            ) as save_info:
                page.locator("#saveViewReact").click()
            checks["save_post_status"] = save_info.value.status
            checks["prompt_message"] = prompts
            page.wait_for_function(
                f"""() => [...document.querySelector('#savedViewSelectReact').options]
                    .some((o) => o.textContent === {json.dumps(view_name)})""",
                timeout=15000,
            )
            view_id = page.evaluate("() => document.querySelector('#savedViewSelectReact').value")
            checks["island_reselects_created"] = bool(view_id)

            # Apply journey: dirty the legacy status filter, then selecting
            # the saved view (stored with clean filters) must rewrite it via
            # the apply bridge and fire a fresh queue request.
            page.evaluate("() => { document.querySelector('#statusFilter').value = 'waiting_human'; }")
            with page.expect_response(lambda r: "/api/conversations?" in r.url):
                page.select_option("#savedViewSelectReact", view_id)
            checks["apply_rewrites_filters"] = page.evaluate(
                "() => document.querySelector('#statusFilter').value === ''"
            )

            # Delete journey: DELETE fires and the island drops the selection.
            with page.expect_response(
                lambda r: "/api/saved-views/" in r.url and r.request.method == "DELETE"
            ):
                page.locator("#deleteViewReact").click()
            checks["delete_request_fired"] = True
            page.wait_for_function(
                "() => document.querySelector('#deleteViewReact')?.disabled === true",
                timeout=15000,
            )
            checks["selection_dropped_after_delete"] = True

            print(json.dumps(checks, ensure_ascii=False, indent=2))
            failed = [k for k, v in checks.items() if v is False or v is None]
            if failed:
                print(f"FAIL: {failed}")
                return 1
            print("PASS: desktop saved views island verified end-to-end")
            return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
