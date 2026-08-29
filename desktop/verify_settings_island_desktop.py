"""Desktop-shell real-machine verification for the settings island (D3 long tail).

Launches the release helix-desktop.exe with a WebView2 remote-debugging port,
opens the settings view over CDP, and asserts the island (not the legacy
cards) renders the desktop runtime readout with the live sidecar port.
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
DEBUG_PORT = 9335
ISLAND_SELECTORS = {
    "island_grid": "#settingsReactIsland .admin-cards",
    "island_port": "#desktopBackendPortReact",
    "island_mode": "#desktopBackendModeReact",
    "island_version": "#desktopVersionReact",
    "legacy_desktop_card": "#settingsDesktopCard",
    "legacy_port": "#desktopBackendPort",
}


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

            page.wait_for_selector("#operatorIdentity", timeout=30000)
            checks: dict[str, object] = {}
            checks["island_mode"] = page.evaluate("() => window.__HELIX_ISLAND_MODE__ === true")
            # The live sidecar origin URL is the port the readout must show.
            origin = page.url.split("/")[2]  # 127.0.0.1:<port>
            checks["origin_port"] = origin

            page.locator('.nav-item[data-view="settings"]').click()
            page.wait_for_selector(ISLAND_SELECTORS["island_grid"], timeout=15000)
            checks["island_grid_mounted"] = True
            checks["legacy_desktop_card_hidden"] = page.evaluate(
                "() => { const c = document.querySelector('#settingsDesktopCard');"
                " return c && c.hidden === true; }"
            )
            checks["version_rendered"] = page.evaluate(
                "() => document.querySelector('#desktopVersionReact')?.textContent"
            )
            checks["port_matches_sidecar_origin"] = page.evaluate(
                "() => document.querySelector('#desktopBackendPortReact')?.textContent"
            ) == f"127.0.0.1:{origin.split(':')[1]}"
            checks["mode_rendered"] = page.evaluate(
                "() => document.querySelector('#desktopBackendModeReact')?.textContent"
            ) == "桌面 sidecar"
            checks["env_note_hidden"] = page.evaluate(
                "() => document.querySelector('#desktopEnvNoteReact')?.hidden === true"
            )
            # The legacy card is yielded: legacy loadDesktopInfo still fills
            # the hidden readout on view switch (by design), so only the
            # card's hidden state matters, not its text.
            checks["legacy_card_yielded"] = page.evaluate(
                "() => { const c = document.querySelector('#settingsDesktopCard');"
                " return c && c.hidden === true; }"
            )

            print(json.dumps(checks, ensure_ascii=False, indent=2))
            failed = [k for k, v in checks.items() if v is False or v is None]
            if failed:
                print(f"FAIL: {failed}")
                return 1
            print("PASS: desktop settings island verified end-to-end")
            return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
