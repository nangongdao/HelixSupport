"""ROADMAP §17.3 tenant administration browser acceptance.

Covers the three promised administration workflows against the real service:
quota read/write, member invite/role/deactivation, and outbound webhook
registration/deletion.  A second page receives an operator-shaped ``/api/me``
response and proves the denied view does not issue privileged requests.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, expect, sync_playwright

from app.assets import STATIC_ASSET_VERSION


BASE_URL = os.getenv("HELIX_BASE_URL", "http://127.0.0.1:8765").rstrip("/")
ARTIFACTS = Path(__file__).resolve().parents[1] / "artifacts"


def attach_failure_recorders(
    page: Page,
    console_errors: list[str],
    page_errors: list[str],
    http_errors: list[str],
    failed_requests: list[str],
) -> None:
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


def open_admin(page: Page) -> None:
    with page.expect_response(
        lambda response: (
            "/api/admin/tenants/" in response.url
            and response.url.endswith("/quota")
            and response.request.method == "GET"
        )
    ):
        page.locator('.nav-item[data-view="admin"]').click()
    expect(page.locator("#adminView")).to_be_visible()
    expect(page.locator("#adminContent")).to_be_visible()


def main() -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    console_errors: list[str] = []
    page_errors: list[str] = []
    http_errors: list[str] = []
    failed_requests: list[str] = []
    run_id = uuid4().hex[:8]
    actor_id = f"browser.admin.{run_id}"
    webhook_url = f"https://example.com/helix/{run_id}"

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except PlaywrightError:
            browser = playwright.chromium.launch(channel="msedge", headless=True)

        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        attach_failure_recorders(page, console_errors, page_errors, http_errors, failed_requests)
        page.goto(BASE_URL, wait_until="domcontentloaded")
        expect(page.locator("#operatorIdentity")).to_contain_text("demo.admin")
        expect(
            page.locator(f'script[src="/static/app.js?v={STATIC_ASSET_VERSION}"]')
        ).to_have_count(1)
        open_admin(page)

        # Form semantics mirror the backend contract and do not expose the
        # signing secret as plain text.
        expect(page.locator("#quotaConversations")).to_have_attribute("min", "1")
        expect(page.locator("#quotaStorageMb")).to_have_attribute("min", "1")
        expect(page.locator("#webhookUrl")).to_have_attribute("type", "url")
        expect(page.locator("#webhookSecret")).to_have_attribute("type", "password")

        # Quota read/write and immediate readout refresh.
        page.locator("#quotaConversations").fill("2500")
        page.locator("#quotaStorageMb").fill("128")
        with page.expect_response(
            lambda response: response.url.endswith("/quota") and response.request.method == "PUT"
        ) as quota_info:
            page.get_by_role("button", name="保存配额").click()
        assert quota_info.value.ok, quota_info.value.text()
        expect(page.locator("#quotaReadout")).to_contain_text("2500")
        expect(page.locator("#quotaReadout")).to_contain_text("128 MB")

        # Member lifecycle: invite -> role change -> deactivate, with every
        # mutation followed by the page's canonical list reload.
        page.locator("#memberActorId").fill(actor_id)
        page.locator("#memberRole").select_option("operator")
        with page.expect_response(
            lambda response: response.url.endswith("/members") and response.request.method == "POST"
        ) as invite_info:
            page.get_by_role("button", name="邀请成员").click()
        assert invite_info.value.status == 201, invite_info.value.text()
        member_row = page.locator("#memberList .admin-member", has_text=actor_id)
        expect(member_row).to_have_count(1)

        with page.expect_response(
            lambda response: (
                response.url.endswith(f"/members/{actor_id}") and response.request.method == "PATCH"
            )
        ) as role_info:
            member_row.locator(".member-role-select").select_option("supervisor")
        assert role_info.value.ok, role_info.value.text()
        member_row = page.locator("#memberList .admin-member", has_text=actor_id)
        expect(member_row).to_contain_text("主管")

        with page.expect_response(
            lambda response: (
                response.url.endswith(f"/members/{actor_id}/deactivate")
                and response.request.method == "POST"
            )
        ) as deactivate_info:
            member_row.get_by_role("button", name="停用").click()
        assert deactivate_info.value.ok, deactivate_info.value.text()
        member_row = page.locator("#memberList .admin-member", has_text=actor_id)
        expect(member_row).to_contain_text("已停用")

        # Event selection is sent verbatim and the endpoint disappears after
        # deletion, proving both registration and tenant-scoped refresh.
        page.locator("#webhookUrl").fill(webhook_url)
        page.locator('#webhookEvents input[value="conversation.created"]').check()
        page.locator("#webhookSecret").fill(f"browser-secret-{run_id}")
        with page.expect_response(
            lambda response: (
                response.url.endswith("/api/webhooks") and response.request.method == "POST"
            )
        ) as webhook_info:
            page.get_by_role("button", name="注册 Webhook").click()
        assert webhook_info.value.status == 201, webhook_info.value.text()
        webhook = webhook_info.value.json()
        hook_row = page.locator("#webhookList .admin-webhook", has_text=webhook_url)
        expect(hook_row).to_contain_text("conversation.created")
        page.screenshot(path=ARTIFACTS / "ui-admin.png", full_page=True)

        with page.expect_response(
            lambda response: (
                response.url.endswith(f"/api/webhooks/{webhook['id']}")
                and response.request.method == "DELETE"
            )
        ) as delete_info:
            # Phase 32.1: the UI now confirms destructive webhook deletions.
            page.on("dialog", lambda dialog: dialog.accept())
            hook_row.get_by_role("button", name="删除").click()
        assert delete_info.value.status == 204, delete_info.value.text()
        expect(page.locator("#webhookList .admin-webhook", has_text=webhook_url)).to_have_count(0)

        # A non-admin sees the explicit denial and, more importantly, never
        # sends a privileged admin/webhook request from the hidden content.
        denied_context = browser.new_context(viewport={"width": 1100, "height": 800})
        denied_page = denied_context.new_page()
        denied_requests: list[str] = []
        denied_page.on(
            "request",
            lambda request: (
                denied_requests.append(f"{request.method} {request.url}")
                if "/api/admin/" in request.url or "/api/webhooks" in request.url
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
        denied_page.goto(BASE_URL, wait_until="domcontentloaded")
        expect(denied_page.locator("#operatorIdentity")).to_contain_text("browser.operator")
        denied_page.locator('.nav-item[data-view="admin"]').click()
        expect(denied_page.locator("#adminDenied")).to_be_visible()
        expect(denied_page.locator("#adminContent")).to_be_hidden()
        denied_page.wait_for_timeout(300)
        assert not denied_requests, denied_requests
        denied_page.screenshot(path=ARTIFACTS / "ui-admin-denied.png", full_page=True)

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
                "admin": str(ARTIFACTS / "ui-admin.png"),
                "denied": str(ARTIFACTS / "ui-admin-denied.png"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
