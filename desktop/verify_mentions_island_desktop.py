"""Desktop-shell real-machine verification for the mentions island (D3 long tail).

Launches the release helix-desktop.exe with a WebView2 remote-debugging port
and drives the mentions inbox with the demo tenant's (empty) inbox: the
badge stays hidden at 0 unread (legacy parity), a programmatic badge click
opens the island panel which renders the legacy empty state after a real
/api/mentions fetch, an outside click closes it, and the legacy badge/panel
stay yielded. The jump/mark-read bridges are covered by component tests —
seeding a real mention needs a second actor session (self-mentions are
skipped by the backend).
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
DEBUG_PORT = 9343


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
            checks["legacy_badge_hidden"] = page.evaluate(
                "() => { const b = document.querySelector('#mentionsBadge');"
                " return b && b.hidden === true; }"
            )
            checks["legacy_panel_hidden"] = page.evaluate(
                "() => { const p = document.querySelector('#mentionsPanel');"
                " return p && p.hidden === true; }"
            )
            checks["island_badge_hidden_at_zero_unread"] = page.evaluate(
                "() => document.querySelector('#mentionsBadgeReactIsland .mentions-badge')?.hidden"
            )

            # Open the panel: at 0 unread the badge is hidden (legacy
            # parity), so use a programmatic click on it.
            page.evaluate(
                """() => document.querySelector('#mentionsBadgeReactIsland .mentions-badge')
                    .dispatchEvent(new MouseEvent('click', { bubbles: true }))"""
            )
            page.wait_for_selector("#mentionsPanelReactIsland .mentions-panel .mentions-heading", timeout=20000)
            checks["panel_opens"] = True
            checks["panel_empty_state"] = page.evaluate(
                "() => document.querySelector('#mentionsPanelReactIsland .queue-empty')?.textContent"
            ) == "暂无被提及"
            checks["badge_visible_while_open"] = page.evaluate(
                "() => document.querySelector('#mentionsBadgeReactIsland .mentions-badge')?.hidden === false"
            )
            checks["real_api_fetched"] = page.evaluate(
                "() => performance.getEntriesByType('resource')"
                ".some((e) => e.name.endsWith('/api/mentions'))"
            )

            # Outside click closes the panel (legacy bindSession parity).
            page.mouse.click(700, 400)
            page.wait_for_function(
                "() => document.querySelector('#mentionsPanelReactIsland .mentions-panel')?.hidden === true",
                timeout=10000,
            )
            checks["outside_click_closes"] = True

            print(json.dumps(checks, ensure_ascii=False, indent=2))
            failed = [k for k, v in checks.items() if v is False or v is None]
            if failed:
                print(f"FAIL: {failed}")
                return 1
            print("PASS: desktop mentions island verified end-to-end")
            return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
