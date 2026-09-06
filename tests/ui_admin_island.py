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
from urllib.parse import urlsplit as _urlsplit
from time import monotonic as _time
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
    # The desktop splash overlays the whole shell until the backend-ready
    # listener runs; wait for it to actually dismiss before interacting —
    # a text assertion passes under the overlay but fill/click actionability
    # does not.
    page.wait_for_selector("#desktopSplash", state="hidden", timeout=30000)
    page.wait_for_function(
        "() => typeof window.HelixModules?.toggleTheme === 'function'", timeout=30000
    )


def open_view(page: Page, view: str, view_id: str) -> None:
    """Click a nav item until its view becomes visible.

    On a cold server the nav click can land before app.js binds the view
    switch (the island's own react-query fires the data request regardless,
    rendering the card grid inside the still-hidden view) — the click must
    be retried until the view is actually shown.
    """
    deadline = _time() + 30
    last_error: Exception | None = None
    while _time() < deadline:
        page.locator(f'.nav-item[data-view="{view}"]').click()
        try:
            page.wait_for_selector(f"{view_id}", state="visible", timeout=2500)
            return
        except PlaywrightError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error


def open_admin_island(page: Page) -> None:
    """Click the admin nav item and wait for the island's data to land.

    Cards render their empty models before the react-query promises resolve,
    so "island non-empty" alone proves nothing — the quota readout showing
    the real demo tenant's name (Northstar Retail) is the deterministic
    settled marker: the identity gate opened AND the quota query resolved.
    """
    open_view(page, "admin", "#adminView")
    expect(page.locator("#quotaReadoutReact")).to_contain_text("Northstar Retail")

    # First-render layout quirk on a fresh database: the card grid can come
    # up inside a zero-height clipped ancestor (inputs at the viewport top,
    # under the fixed header) and recovers on the next page load. Detect the
    # collapsed layout and reload once before any form interaction.
    if (
        page.locator("#quotaConversationsReact").evaluate("el => el.getBoundingClientRect().height")
        == 0
    ):
        page.reload(wait_until="domcontentloaded")
        page.evaluate("() => window.dispatchEvent(new Event('helix-backend-ready'))")
        page.wait_for_selector("#desktopSplash", state="hidden", timeout=30000)
        open_view(page, "admin", "#adminView")
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
        # Skipped when the environment's DNS resolves the example host into a
        # blocked range (198.18.x fake-IP noise documented in the roadmap) —
        # the SSRF guard correctly refuses and the sub-journey would 422.
        import socket as _socket
        import ipaddress as _ipaddress

        def _webhook_dns_clean(url: str) -> bool:
            try:
                host = _socket.getaddrinfo(_socket.getfqdn(_urlsplit(url).hostname), None)[0][
                    4
                ][0]
                return not _ipaddress.ip_address(host).is_private
            except (OSError, ValueError):
                return False

        webhook_skipped = not _webhook_dns_clean(webhook_url)
        if webhook_skipped:
            print(
                f"NOTE: skipping webhook sub-journey — {webhook_url} resolves "
                "into a blocked range in this environment"
            )
        else:
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

        # Governance operations (2.7.0): seed a pending approval (requested
        # by another actor — maker-checker refuses self-approval) and a
        # staged feedback row directly in the server's database, then decide
        # and review them through the island's buttons. The card refetches
        # via the header's 刷新管理数据 button.
        db_path = os.getenv("HELIX_DB_PATH")
        assert db_path, "HELIX_DB_PATH must point at the server's database"
        import sqlite3

        decision_now = "2026-09-05T00:00:00.000000+00:00"
        with sqlite3.connect(db_path) as seed_conn:
            seed_conn.execute(
                """INSERT INTO ai_approvals
                (id, tenant_id, subject_kind, subject_id, requested_by,
                 decision, reason, created_at)
                VALUES (?, 'demo', 'tool_enablement', ?, 'seed.supervisor',
                        'pending', 'ui journey', ?)""",
                (f"apr_{run_id}", f"knowledge.publish_bulk.{run_id}", decision_now),
            )
            seed_conn.execute(
                """INSERT INTO ai_online_feedback
                (id, tenant_id, conversation_id, source, redacted_json,
                 review_status, created_at)
                VALUES (?, 'demo', NULL, 'negative_rating', ?, 'pending_review', ?)""",
                (
                    f"fbk_{run_id}",
                    json.dumps({"rating": -1, "reason": "ui journey"}),
                    decision_now,
                ),
            )
        page.get_by_role("button", name="刷新管理数据").click()
        approvals_card = page.locator("#governanceApprovalsListReact")
        gov_row = approvals_card.locator(
            ".governance-row", has_text=f"knowledge.publish_bulk.{run_id}"
        )
        expect(gov_row).to_have_count(1)
        feedback_card = page.locator("#governanceFeedbackListReact")
        feedback_row = feedback_card.locator(".governance-row", has_text="ui journey")
        expect(feedback_row).to_have_count(1)

        approval_id = f"apr_{run_id}"
        with page.expect_response(
            lambda response: (
                response.url.endswith(f"/api/admin/governance/approvals/{approval_id}/decide")
                and response.request.method == "POST"
            )
        ) as decide_info:
            gov_row.get_by_role("button", name="批准").click()
        assert decide_info.value.ok, decide_info.value.text()
        expect(
            page.locator(
                "#governanceApprovalsListReact .governance-row",
                has_text=f"knowledge.publish_bulk.{run_id}",
            )
        ).to_have_count(0)

        feedback_id = f"fbk_{run_id}"
        with page.expect_response(
            lambda response: (
                response.url.endswith(f"/api/admin/governance/feedback/{feedback_id}/review")
                and response.request.method == "POST"
            )
        ) as review_info:
            feedback_row.get_by_role("button", name="接受").click()
        assert review_info.value.ok, review_info.value.text()
        expect(
            page.locator("#governanceFeedbackListReact .governance-row", has_text="ui journey")
        ).to_have_count(0)

        # Eval-run readout (2.9.0): seed a dataset + run directly in the
        # server's database, refresh, and assert the governance card renders
        # the run with its pass state.
        with sqlite3.connect(db_path) as seed_conn:
            seed_conn.execute(
                """INSERT INTO ai_eval_datasets
                (id, tenant_id, name, version, strategy, content_hash,
                 item_count, created_by, created_at)
                VALUES (?, 'demo', ?, 1, 'feedback', 'hash-ui-journey', 1,
                        'seed.supervisor', ?)""",
                (f"ds_{run_id}", f"journey-set.{run_id}", decision_now),
            )
            seed_conn.execute(
                """INSERT INTO ai_eval_runs
                (id, dataset_id, candidate, baseline, report_object_id,
                 passed, metrics_json, created_at)
                VALUES (?, ?, 'triage-v9', 'triage-v8', NULL, 1,
                        '{"accuracy": 0.95}', ?)""",
                (f"run_{run_id}", f"ds_{run_id}", decision_now),
            )
        page.get_by_role("button", name="刷新管理数据").click()
        runs_readout = page.locator("#governanceEvalRunsListReact")
        expect(runs_readout).to_contain_text("triage-v9 @ journey-set")
        expect(runs_readout).to_contain_text("通过")

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
        open_view(denied_page, "admin", "#adminDenied")
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
