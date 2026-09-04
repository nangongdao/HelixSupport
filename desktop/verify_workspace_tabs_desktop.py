"""Desktop-shell real-machine verification for the workspace tabs island (D3 long tail).

Launches the release helix-desktop.exe with a WebView2 remote-debugging port
and drives the 队列/工单 tablist through the island: optimistic switch, the
legacy pane actually following (queuePane dataset.mode), the island
reconciling on a programmatic switch back, and the legacy tablist yielded.
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
DEBUG_PORT = 9341


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
            checks["legacy_tablist_hidden"] = page.evaluate(
                "() => { const t = document.querySelector('#workspaceTabs');"
                " return t && t.hidden === true; }"
            )
            checks["island_tablist_present"] = page.evaluate(
                "() => Boolean(document.querySelector('#workspaceTabsReactIsland .workspace-tabs'))"
            )

            # Click 工单 in the island: optimistic active state…
            page.locator("#workspaceTabsReactIsland button", has_text="工单").click()
            page.wait_for_function(
                "() => document.querySelector('#workspaceTabsReactIsland button[data-wstab=tickets]')"
                ".classList.contains('is-active')",
                timeout=10000,
            )
            checks["island_switches_to_tickets"] = True
            # …and the legacy pane follows (dataset.mode + ticket pane shown).
            page.wait_for_function(
                "() => document.querySelector('#queuePane')?.dataset.mode === 'tickets'",
                timeout=10000,
            )
            checks["legacy_pane_follows"] = True

            # Switch back to 队列.
            page.locator("#workspaceTabsReactIsland button", has_text="队列").click()
            page.wait_for_function(
                "() => document.querySelector('#queuePane')?.dataset.mode === 'queue'",
                timeout=10000,
            )
            checks["switch_back_to_queue"] = True

            # Programmatic reconciliation: fire the legacy switch directly and
            # let the changed event drive the island.
            page.evaluate(
                "() => window.HelixModules?.ticketView?.switchWorkspaceTab?.('tickets')"
            )
            page.wait_for_function(
                "() => document.querySelector('#workspaceTabsReactIsland button[data-wstab=tickets]')"
                ".classList.contains('is-active')",
                timeout=10000,
            )
            checks["island_reconciles_programmatic"] = True

            print(json.dumps(checks, ensure_ascii=False, indent=2))
            failed = [k for k, v in checks.items() if v is False or v is None]
            if failed:
                print(f"FAIL: {failed}")
                return 1
            print("PASS: desktop workspace tabs island verified end-to-end")
            return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
