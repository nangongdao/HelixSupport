"""ROADMAP section 17 knowledge operations browser acceptance.

Exercises the real writer journey (draft, edit, filter, publish, retire) and
proves a read-only operator only requests the public article list and receives
no lifecycle controls.
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
    def record_console(message) -> None:
        if message.type == "error":
            console_errors.append(f"{message.text} @ {message.location}")

    def record_failed_request(request) -> None:
        failure = request.failure or ""
        if request.url.startswith(f"{BASE_URL}/api/events/queue") and "ERR_ABORTED" in failure:
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


def open_knowledge(page: Page, *, include_inactive: bool) -> None:
    expected_suffix = (
        "/api/knowledge?include_inactive=true" if include_inactive else "/api/knowledge"
    )
    with page.expect_response(
        lambda response: (
            response.url == f"{BASE_URL}{expected_suffix}" and response.request.method == "GET"
        )
    ) as response_info:
        page.locator('.nav-item[data-view="knowledge"]').click()
    assert response_info.value.ok, response_info.value.text()
    expect(page.locator("#knowledgeView")).to_be_visible()
    expect(page.locator("#placeholderView")).to_be_hidden()


def main() -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    console_errors: list[str] = []
    page_errors: list[str] = []
    http_errors: list[str] = []
    failed_requests: list[str] = []
    run_id = uuid4().hex[:8]
    title = f"浏览器知识草稿 {run_id}"
    revised_content = f"这是通过真实浏览器更新的知识正文，运行标识 {run_id}。"

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
        expect(page.locator('script[src="/static/app.js?v=1.3.7"]')).to_have_count(1)
        open_knowledge(page, include_inactive=True)

        expect(page.locator("#newKnowledgeDraft")).to_be_visible()
        page.locator("#newKnowledgeDraft").click()
        expect(page.locator("#knowledgeEditor")).to_be_visible()
        page.locator("#knowledgeTitle").fill(title)
        page.locator("#knowledgeContent").fill(
            "这是一条等待审核的知识正文，长度满足服务端验证要求。"
        )
        page.locator("#knowledgeTags").fill(f"browser, {run_id}, 配送")
        page.locator("#knowledgeCategory").fill("browser-acceptance")
        page.locator("#knowledgeLanguage").select_option("zh")
        page.locator("#knowledgeSource").fill(f"https://example.com/knowledge/{run_id}")
        with page.expect_response(
            lambda response: (
                response.url.endswith("/api/knowledge/drafts") and response.request.method == "POST"
            )
        ) as create_info:
            page.get_by_role("button", name="保存草稿").click()
        assert create_info.value.status == 201, create_info.value.text()
        article = create_info.value.json()

        row = page.locator(".knowledge-article", has_text=title)
        expect(row).to_have_count(1)
        expect(row.locator(".knowledge-status")).to_have_text("草稿")

        # Search, status, and language filters compose without another API
        # request; the unique draft remains the only visible result.
        page.locator("#knowledgeSearch").fill(run_id)
        page.locator("#knowledgeStatusFilter").select_option("draft")
        page.locator("#knowledgeLanguageFilter").select_option("zh")
        expect(page.locator("#knowledgeResultCount")).to_contain_text("1 /")
        expect(row).to_be_visible()

        row.get_by_role("button", name="编辑").click()
        expect(page.locator("#knowledgeEditorTitle")).to_have_text("编辑知识文章")
        page.locator("#knowledgeContent").fill(revised_content)
        with page.expect_response(
            lambda response: (
                response.url.endswith(f"/api/knowledge/{article['id']}")
                and response.request.method == "PATCH"
            )
        ) as update_info:
            page.get_by_role("button", name="保存修改").click()
        assert update_info.value.ok, update_info.value.text()
        row = page.locator(".knowledge-article", has_text=title)
        row.get_by_text("查看正文").click()
        expect(row.locator(".knowledge-article-content")).to_have_text(revised_content)

        with page.expect_response(
            lambda response: (
                response.url.endswith(f"/api/knowledge/{article['id']}/review")
                and response.request.method == "POST"
            )
        ) as publish_info:
            row.get_by_role("button", name="发布").click()
        assert publish_info.value.ok, publish_info.value.text()
        page.locator("#knowledgeStatusFilter").select_option("published")
        row = page.locator(".knowledge-article", has_text=title)
        expect(row.locator(".knowledge-status")).to_have_text("已发布")
        expect(page.locator("#toast")).to_be_hidden(timeout=5000)
        page.screenshot(path=ARTIFACTS / "ui-knowledge.png", full_page=True)

        # The same view remains usable at a narrow operator viewport.
        page.set_viewport_size({"width": 390, "height": 844})
        expect(row).to_be_visible()
        assert page.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
        )
        page.screenshot(path=ARTIFACTS / "ui-knowledge-mobile.png", full_page=True)

        row.get_by_role("button", name="编辑").click()
        editor = page.locator("#knowledgeEditor")
        expect(editor).to_be_visible()
        editor.scroll_into_view_if_needed()
        editor_box = editor.bounding_box()
        assert editor_box is not None
        assert editor_box["x"] >= 0
        assert editor_box["x"] + editor_box["width"] <= 390
        page.screenshot(path=ARTIFACTS / "ui-knowledge-mobile-editor.png", full_page=True)
        page.locator("#cancelKnowledgeEdit").click()
        row.scroll_into_view_if_needed()

        page.on("dialog", lambda dialog: dialog.accept())
        with page.expect_response(
            lambda response: (
                response.url.endswith(f"/api/knowledge/{article['id']}/review")
                and response.request.method == "POST"
            )
        ) as retire_info:
            row.get_by_role("button", name="停用").click()
        assert retire_info.value.ok, retire_info.value.text()
        page.locator("#knowledgeStatusFilter").select_option("retired")
        row = page.locator(".knowledge-article", has_text=title)
        expect(row.locator(".knowledge-status")).to_have_text("已停用")

        # Intercept /api/me as an operator. The app must request only the
        # public listing and must never expose writer controls.
        reader_context = browser.new_context(viewport={"width": 1100, "height": 800})
        reader_page = reader_context.new_page()
        reader_requests: list[str] = []
        reader_page.on(
            "request",
            lambda request: (
                reader_requests.append(f"{request.method} {request.url}")
                if "/api/knowledge" in request.url
                else None
            ),
        )
        reader_page.route(
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
        reader_page.goto(BASE_URL, wait_until="domcontentloaded")
        expect(reader_page.locator("#operatorIdentity")).to_contain_text("browser.operator")
        open_knowledge(reader_page, include_inactive=False)
        expect(reader_page.locator("#knowledgeReadOnly")).to_be_visible()
        expect(reader_page.locator("#newKnowledgeDraft")).to_be_hidden()
        expect(reader_page.locator("#knowledgeEditor")).to_be_hidden()
        expect(reader_page.locator(".knowledge-action")).to_have_count(0)
        reader_page.wait_for_timeout(250)
        assert reader_requests == [f"GET {BASE_URL}/api/knowledge"], reader_requests

        reader_context.close()
        context.close()
        browser.close()

    assert not console_errors, console_errors
    assert not page_errors, page_errors
    assert not http_errors, http_errors
    assert not failed_requests, failed_requests
    print("Knowledge operations browser acceptance passed")


if __name__ == "__main__":
    main()
