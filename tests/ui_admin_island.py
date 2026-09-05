"""Desktop-shell admin write journeys (island-mode ui_admin).

tests/ui_admin.py drives the legacy-rendered console; in the desktop shell
the admin island owns every card and all writes travel the helix-admin-*
bridge events into the legacy api() handlers (js/admin-actions.js). Those
bridges were only covered by vitest with a mocked backend — this suite
exercises the full loop against the real service in the mounted shell:

island form → bridge event → legacy handler → real API call →
helix-admin-saved → island react-query refetch → re-rendered island DOM.

Journeys: quota save (island readout refetch), member invite → role change
→ deactivation, webhook registration → confirmed deletion, and the
non-admin invariant (island renders nothing and never issues a privileged
request — the read-side gate the island enforces with ``enabled: false``).

Boot follows tests/ui_accessibility.py's desktop-shell pass: the Tauri
preconditions are injected before goto so the islands mount, then the
backend-ready event releases the shell.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, expect, sync_playwright

BASE_URL = os.getenv("HELIX_BASE_URL", "http://127.0.0.1:8765").rstrip("/")
ARTIFACTS = Path(__file__).resolve().parents[1] / "artifacts"


def attach_failure_recorders(
    page: Page,
    console_errors: list[str],
    page_errors: list[str],
    http_errors: list[str],
    failed_requests: list[str],
) -> None:
    """Mirror ui_admin's recorders: any console error, page error, HTTP >= 400
    or aborted request fails the run (SSE aborts and DELETE races excepted)."""

    def record_console(message) -> None:
        if message.type == "error":
            console_errors.append(f"{message.text} @ {message.location}")

    def record_failed_request(request) -> None:
        failure = request.failure or ""
        if request.url.startswith(f"{BASE_URL}/api/events/queue") and "ERR_ABORTED" in failure:
            return
        if request.method == "DELETE" and "ERR_ABORTED" in failure:
            return
        failed_requests.append(f"{request.method} {request.url} {failure}")

    page.on("console", record_console)
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.on("requestfailed", record_failed_request)
    page.on(
        "response",
        lambda response: (
            http_errors.append(f"{response.request.method} {response.status} {response.url}")
            if response.status >= 400
            else None
        ),
    )


def boot_shell(page: Page) -> None:
    """Desktop preconditions before goto, then release the shell."""
    page.add_init_script("window.__TAURI_INTERNALS__ = { invoke: () => Promise.resolve() };")
    page.goto(BASE_URL, wait_until="domcontentloaded")
    page.evaluate("() => window.dispatchEvent(new Event('helix-backend-ready'))")
    page.wait_for_function(
        "() => typeof window.HelixModules?.toggleTheme === 'function'", timeout=30000
    )


def open_admin_island(page: Page) -> None:
    """Click the admin nav item and wait for the island's own data to land.

    Cards render their empty models before the react-query promises resolve,
    so "island non-empty" alone proves nothing — the quota readout showing
    the tenant name is the deterministic settled marker.
    """
    with page.expect_response(
        lambda response: (
            "/api/admin/tenants/" in response.url
            and response.url.endswith("/quota")
            and response.request.method == "GET"
        )
    ):
        page.locator('.nav-item[data-view="admin"]').click()
    page.wait_for_selector("#adminReactIsland:not(:empty)", timeout=30000)
    # The real demo tenant's name — the settled marker that the island's
    # identity gate opened AND the quota query resolved.
    expect(page.locator("#quotaReadoutReact")).to_contain_text("Northstar Retail")


def main() -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    console_errors: list[str] = []
    page_errors: list[str] = []
    http_errors: list[str] = []
    failed_requests: list[str] = []
    run_id = uuid4().hex[:8]
    actor_id = f"island.admin.{run_id}"
    webhook_url = f"https://example.com/helix/{run_id}"

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except PlaywrightError:
            browser = playwright.chromium.launch(channel="msedge", headless=True)

        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        attach_failure_recorders(page, console_errors, page_errors, http_errors, failed_requests)
        boot_shell(page)
        open_admin_island(page)

        # Island form semantics mirror the legacy contract.
        expect(page.locator("#quotaConversationsReact")).to_have_attribute("min", "1")
        expect(page.locator("#quotaStorageMbReact")).to_have_attribute("min", "1")
        expect(page.locator("#webhookUrlReact")).to_have_attribute("type", "url")
        expect(page.locator("#webhookSecretReact")).to_have_attribute("type", "password")

        # Quota: island submit → bridge → PUT → saved event → island refetch.
        page.locator("#quotaConversationsReact").fill("2500")
        page.locator("#quotaStorageMbReact").fill("128")
        with page.expect_response(
            lambda response: response.url.endswith("/quota") and response.request.method == "PUT"
        ) as quota_info:
            page.get_by_role("button", name="保存配额").click()
        assert quota_info.value.ok, quota_info.value.text()
        # The island readout must re-render from the refetched quota, proving
        # the saved-event → invalidateQueries → GET → render loop, not just
        # the PUT.
        expect(page.locator("#quotaReadoutReact")).to_contain_text("2500")
        expect(page.locator("#quotaReadoutReact")).to_contain_text("128 MB")

        # Member lifecycle on the island list: invite → role → deactivate.
        page.locator("#memberActorIdReact").fill(actor_id)
        page.locator("#memberRoleReact").select_option("operator")
        with page.expect_response(
            lambda response: response.url.endswith("/members") and response.request.method == "POST"
        ) as invite_info:
            page.get_by_role("button", name="邀请成员").click()
        assert invite_info.value.status == 201, invite_info.value.text()
        member_row = page.locator("#memberListReact .admin-member", has_text=actor_id)
        expect(member_row).to_have_count(1)

        with page.expect_response(
            lambda response: (
                response.url.endswith(f"/members/{actor_id}") and response.request.method == "PATCH"
            )
        ) as role_info:
            member_row.locator(".member-role-select").select_option("supervisor")
        assert role_info.value.ok, role_info.value.text()
        member_row = page.locator("#memberListReact .admin-member", has_text=actor_id)
        expect(member_row).to_contain_text("主管")

        with page.expect_response(
            lambda response: (
                response.url.endswith(f"/members/{actor_id}/deactivate")
                and response.request.method == "POST"
            )
        ) as deactivate_info:
            member_row.get_by_role("button", name="停用").click()
        assert deactivate_info.value.ok, deactivate_info.value.text()
        member_row = page.locator("#memberListReact .admin-member", has_text=actor_id)
        expect(member_row).to_contain_text("已停用")

        # Webhook registration and confirmed deletion on the island list.
        page.locator("#webhookUrlReact").fill(webhook_url)
        page.locator('#webhookEventsReact input[value="conversation.created"]').check()
        page.locator("#webhookSecretReact").fill(f"browser-secret-{run_id}")
        with page.expect_response(
            lambda response: (
                response.url.endswith("/api/webhooks") and response.request.method == "POST"
            )
        ) as webhook_info:
            page.get_by_role("button", name="注册 Webhook").click()
        assert webhook_info.value.status == 201, webhook_info.value.text()
        webhook = webhook_info.value.json()
        hook_row = page.locator("#webhookListReact .admin-webhook", has_text=webhook_url)
        expect(hook_row).to_contain_text("conversation.created")
        page.screenshot(path=ARTIFACTS / "ui-admin-island.png", full_page=True)

        with page.expect_response(
            lambda response: (
                response.url.endswith(f"/api/webhooks/{webhook['id']}")
                and response.request.method == "DELETE"
            )
        ) as delete_info:
            # The island's delete bridge lets legacy own window.confirm.
            page.on("dialog", lambda dialog: dialog.accept())
            hook_row.get_by_role("button", name="删除").click()
        assert delete_info.value.status == 204, delete_info.value.text()
        expect(
            page.locator("#webhookListReact .admin-webhook", has_text=webhook_url)
        ).to_have_count(0)

        # Non-admin in the shell: the island renders nothing (enabled:false)
        # and, unlike the read path, not a single privileged request fires.
        denied_context = browser.new_context(viewport={"width": 1100, "height": 800})
        denied_page = denied_context.new_page()
        attach_failure_recorders(
            denied_page, console_errors, page_errors, http_errors, failed_requests
        )
        denied_requests: list[str] = []
        denied_page.on(
            "request",
            lambda request: (
                denied_requests.append(f"{request.method} {request.url}")
                if "/api/admin/" in request.url
                or "/api/webhooks" in request.url
                or "/api/analytics/" in request.url
                else None
            ),
        )
        denied_page.route(
            "**/api/me",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "tenant_id": "demo",
                        "actor_id": "browser.operator",
                        "role": "operator",
                        "permissions": [
                            "conversation:read",
                            "conversation:write",
                            "operator:act",
                        ],
                        "local_drafts_enabled": True,
                        "local_draft_ttl_minutes": 720,
                        "credential_id": None,
                    }
                ),
            ),
        )
        boot_shell(denied_page)
        denied_page.locator('.nav-item[data-view="admin"]').click()
        expect(denied_page.locator("#adminDenied")).to_be_visible()
        expect(denied_page.locator("#adminContent")).to_be_hidden()
        denied_page.wait_for_timeout(500)
        assert not denied_requests, denied_requests
        denied_page.screenshot(path=ARTIFACTS / "ui-admin-island-denied.png", full_page=True)

        denied_context.close()
        context.close()
        browser.close()

    assert not console_errors, console_errors
    assert not page_errors, page_errors
    assert not http_errors, http_errors
    assert not failed_requests, failed_requests
    print(
        json.dumps(
            {
                "status": "ok",
                "actor_id": actor_id,
                "admin": str(ARTIFACTS / "ui-admin-island.png"),
                "denied": str(ARTIFACTS / "ui-admin-island-denied.png"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
