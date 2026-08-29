"""Desktop-shell real-machine verification for the identity island (D3 long tail).

Launches the release helix-desktop.exe with a WebView2 remote-debugging port
and asserts the header identity readout renders through the React island
("actor · role"), with the legacy #operatorIdentity span yielded.
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
DEBUG_PORT = 9338


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

            # The legacy span is yielded (hidden) by this very slice — wait
            # for attachment, not visibility.
            page.wait_for_selector("#operatorIdentity", state="attached", timeout=30000)
            checks: dict[str, object] = {}
            checks["island_mode"] = page.evaluate("() => window.__HELIX_ISLAND_MODE__ === true")

            # The identity island renders as soon as the first refreshAll
            # cycle publishes helix-identity (demo.admin in the shell).
            page.wait_for_function(
                "() => /^.+ · .+$/.test("
                "document.querySelector('#identityReactIsland .operator-identity')?.textContent || '')",
                timeout=30000,
            )
            readout = page.evaluate(
                "() => document.querySelector('#identityReactIsland .operator-identity')?.textContent"
            )
            checks["island_readout"] = readout
            checks["readout_format"] = bool(readout and " · " in readout)
            checks["legacy_span_hidden"] = page.evaluate(
                "() => { const s = document.querySelector('#operatorIdentity');"
                " return s && s.hidden === true; }"
            )
            checks["legacy_span_not_painted"] = page.evaluate(
                "() => document.querySelector('#operatorIdentity')?.textContent === '正在验证'"
            )
            # The header toggles must remain present and functional-looking.
            checks["header_toggles_kept"] = page.evaluate(
                "() => Boolean(document.querySelector('#themeToggle')"
                " && document.querySelector('#refreshList')"
                " && document.querySelector('#lowPerfToggle'))"
            )

            print(json.dumps(checks, ensure_ascii=False, indent=2))
            failed = [k for k, v in checks.items() if v is False or v is None]
            if failed:
                print(f"FAIL: {failed}")
                return 1
            print("PASS: desktop identity island verified end-to-end")
            return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
