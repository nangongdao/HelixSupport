"""Desktop-shell real-machine verification for the palette command wiring (D3 long tail).

Launches the release helix-desktop.exe with a WebView2 remote-debugging port
and drives the Ctrl+K palette island end-to-end: running nav:admin switches
to the admin view (island-rendered), conv:refresh triggers a queue request,
and diag:logs opens the terminal drawer. Closes the loop on the previously
inert helix-command events.
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
DEBUG_PORT = 9344


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


def open_palette(page) -> None:
    """Ctrl+K until the palette input appears.

    Islands mount sequentially, and the palette island is late in the
    ISLANDS array — a Ctrl+K pressed before its keydown handler registers
    is simply lost, so retry until the input is there.
    """
    for _ in range(10):
        page.keyboard.press("Control+k")
        try:
            page.wait_for_selector(
                "#commandPaletteReactIsland .command-input", timeout=2000
            )
            return
        except Exception:
            page.keyboard.press("Escape")
    raise RuntimeError("palette island never opened")


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

            # Open the palette island with Ctrl+K.
            open_palette(page)
            checks["palette_opens"] = True

            # Run nav:admin — the admin island view must open.
            page.fill("#commandPaletteReactIsland .command-input", "管理")
            page.wait_for_selector("#commandPaletteReactIsland .command-item", timeout=10000)
            page.locator("#commandPaletteReactIsland .command-item", has_text="管理").first.click()
            page.wait_for_selector("#adminReactIsland .admin-cards", timeout=15000)
            checks["nav_admin_opens_admin_view"] = page.evaluate(
                "() => { const v = document.querySelector('#adminView');"
                " return v && !v.hidden; }"
            )

            # Run conv:refresh — a fresh queue request must fire.
            open_palette(page)
            page.fill("#commandPaletteReactIsland .command-input", "刷新队列")
            page.wait_for_selector("#commandPaletteReactIsland .command-item", timeout=10000)
            before = page.evaluate(
                "() => performance.getEntriesByType('resource')"
                ".filter((e) => e.name.includes('/api/conversations?')).length"
            )
            page.locator("#commandPaletteReactIsland .command-item", has_text="刷新队列").first.click()
            page.wait_for_function(
                """(before) => performance.getEntriesByType('resource')
                    .filter((e) => e.name.includes('/api/conversations?')).length > before""",
                arg=before,
                timeout=20000,
            )
            checks["conv_refresh_refetches"] = True

            # Run diag:logs — the terminal drawer must open.
            open_palette(page)
            page.fill("#commandPaletteReactIsland .command-input", "服务器日志")
            page.wait_for_selector("#commandPaletteReactIsland .command-item", timeout=10000)
            page.locator("#commandPaletteReactIsland .command-item", has_text="服务器日志").first.click()
            page.wait_for_selector("#terminalReactIsland .terminal-drawer, #terminalReactIsland .terminal-wrap, #terminalReactIsland canvas, #terminalReactIsland .xterm", timeout=15000)
            checks["diag_logs_opens_terminal"] = True

            print(json.dumps(checks, ensure_ascii=False, indent=2))
            failed = [k for k, v in checks.items() if v is False or v is None]
            if failed:
                print(f"FAIL: {failed}")
                return 1
            print("PASS: desktop palette command wiring verified end-to-end")
            return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
