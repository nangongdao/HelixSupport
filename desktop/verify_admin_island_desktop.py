"""Desktop-shell real-machine verification for the admin island (D3 long tail).

Launches the release helix-desktop.exe with a WebView2 remote-debugging port,
drives the admin view through CDP, and asserts the island (not the legacy
cards) renders the whole #adminContent grid — including a real member invite
through the helix-admin-invite-member bridge.
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
DEBUG_PORT = 9334
ISLAND_SELECTORS = {
    "island_grid": "#adminReactIsland .admin-cards",
    "island_quota_readout": "#quotaReadoutReact",
    "island_member_list": "#memberListReact",
    "island_member_form": "#memberFormReact",
    "legacy_quota_card": "#adminQuotaCard",
}


def wait_for_cdp(deadline_s: float = 90.0) -> list[dict]:
    """Wait until the WebView2 exposes a sidecar-origin (127.0.0.1) page.

    The shell first shows the tauri://localhost splash and navigates to the
    sidecar origin once the backend is ready — a cold PyInstaller start can
    take well past 30s on a busy workstation, so the deadline is generous.
    """
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
            checks["identity"] = page.text_content("#operatorIdentity") or ""

            # Open the admin view; the island mounts at page load but its
            # queries only fire once helix-identity reports admin:manage.
            page.locator('.nav-item[data-view="admin"]').click()
            page.wait_for_selector(ISLAND_SELECTORS["island_grid"], timeout=15000)
            checks["island_grid_mounted"] = True
            checks["legacy_quota_card_hidden"] = page.evaluate(
                "() => { const c = document.querySelector('#adminQuotaCard');"
                " return c && c.hidden === true; }"
            )
            page.wait_for_function(
                "() => document.querySelector('#quotaReadoutReact')?.textContent.includes('租户')",
                timeout=15000,
            )
            checks["quota_readout_rendered"] = True
            checks["denied_panel_hidden"] = page.evaluate(
                "() => { const d = document.querySelector('#adminDenied');"
                " return d && d.hidden === true; }"
            )

            # Writer journey inside the island: invite a member through the
            # bridged form and see the list refetch with the new row.
            run_id = uuid4().hex[:6]
            actor_id = f"island.admin.{run_id}"
            page.fill("#memberActorIdReact", actor_id)
            page.select_option("#memberRoleReact", "supervisor")
            with page.expect_response(
                lambda r: r.url.endswith("/members") and r.request.method == "POST"
            ):
                page.locator("#memberFormReact button[type='submit']").click()
            page.wait_for_selector(
                f"#memberListReact .admin-member:has-text('{actor_id}')", timeout=15000
            )
            row = page.locator("#memberListReact .admin-member", has_text=actor_id)
            checks["member_invited_via_bridge"] = True
            checks["member_role_label"] = "主管" in (row.text_content() or "")

            print(json.dumps(checks, ensure_ascii=False, indent=2))
            failed = [k for k, v in checks.items() if v is False or v is None]
            if failed:
                print(f"FAIL: {failed}")
                return 1
            print("PASS: desktop admin island verified end-to-end")
            return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
