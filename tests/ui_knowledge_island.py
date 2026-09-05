"""Knowledge authoring journey in the DESKTOP SHELL (islands mounted).

ui_knowledge.py drives the same journey in web mode against the legacy
renderer; this variant boots the shell so the knowledge island owns the
whole surface (summary, filter toolbar, article list, draft editor) and
proves the authoring loop against the real backend: create draft →
search/filter → edit → publish → mobile viewport → retire → and the
reader invariant (an operator session never sees writer controls).
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


def open_knowledge_island(page: Page) -> None:
    """Click the knowledge nav item and wait for the island's own list.

    The island fetches /api/knowledge at boot (before the nav click), so
    the settled marker is rendered article rows, not the response.
    """
    page.locator('.nav-item[data-view="knowledge"]').click()
    page.wait_for_selector("#knowledgeReactIsland:not(:empty)", timeout=30000)
    expect(page.locator("#knowledgeReactIsland .knowledge-article").first).to_be_visible(
        timeout=30000
    )


def main() -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    console_errors: list[str] = []
    page_errors: list[str] = []
    http_errors: list[str] = []
    failed_requests: list[str] = []
    run_id = uuid4().hex[:6]
    title = f"桌面壳知识验收 {run_id}"
    revised_content = (
        f"桌面壳验收修订后的正文 {run_id}：首单配送时效承诺为 48 小时，偏远地区顺延两个工作日。"
    )

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except PlaywrightError:
            browser = playwright.chromium.launch(channel="msedge", headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        attach_failure_recorders(page, console_errors, page_errors, http_errors, failed_requests)
        page.add_init_script("window.__TAURI_INTERNALS__ = { invoke: () => Promise.resolve() };")
        page.goto(BASE_URL, wait_until="domcontentloaded")
        page.evaluate("() => window.dispatchEvent(new Event('helix-backend-ready'))")
        expect(page.get_by_text("demo.admin", exact=False).first).to_be_visible(timeout=30000)
        open_knowledge_island(page)

        island = page.locator("#knowledgeReactIsland")

        # Create a draft through the island editor (the 新建草稿 button stays
        # legacy and bridges helix-knowledge-new).
        # The 新建草稿 button stays legacy (yields nothing); it bridges
        # helix-knowledge-new and the island opens its editor.
        page.locator("#newKnowledgeDraft").click()
        editor_title = island.locator("#knowledgeEditorTitleReact")
        expect(editor_title).to_have_text("新建知识草稿")
        island.locator("#knowledgeTitleReact").fill(title)
        island.locator("#knowledgeContentReact").fill(
            f"桌面壳验收正文 {run_id}：首单配送时效承诺为 72 小时。"
        )
        island.locator("#knowledgeTagsReact").fill(f"browser, {run_id}, 配送")
        island.locator("#knowledgeCategoryReact").fill("browser-acceptance")
        island.locator("#knowledgeLanguageReact").select_option("zh")
        island.locator("#knowledgeSourceReact").fill(f"https://example.com/knowledge/{run_id}")
        with page.expect_response(
            lambda response: (
                response.url.endswith("/api/knowledge/drafts") and response.request.method == "POST"
            )
        ) as save_info:
            island.get_by_role("button", name="保存草稿").click()
        assert save_info.value.status == 201, save_info.value.text()
        article = save_info.value.json()
        assert article.get("id"), article

        row = island.locator(".knowledge-article", has_text=title)
        expect(row).to_have_count(1)
        expect(row.locator(".knowledge-status")).to_have_text("草稿")

        # Search + filters are controlled island components (no ids): the
        # search input carries a placeholder; the status/language selects
        # are the two .knowledge-filter selects in toolbar order.
        island.get_by_placeholder("搜索标题、正文、标签或来源").fill(run_id)
        island.locator(".knowledge-filter select").nth(0).select_option("draft")
        island.locator(".knowledge-filter select").nth(1).select_option("zh")
        expect(island.get_by_text("1 /").first).to_be_visible()

        # Edit and save the revision.
        row.get_by_role("button", name="编辑").click()
        expect(island.locator("#knowledgeEditorTitleReact")).to_have_text("编辑知识文章")
        island.locator("#knowledgeContentReact").fill(revised_content)
        with page.expect_response(
            lambda response: (
                response.url.endswith(f"/api/knowledge/{article['id']}")
                and response.request.method == "PATCH"
            )
        ) as update_info:
            island.get_by_role("button", name="保存修改").click()
        assert update_info.value.ok, update_info.value.text()
        row.get_by_text("查看正文").click()
        expect(row.locator(".knowledge-article-content")).to_have_text(revised_content)

        # Publish through the review bridge.
        with page.expect_response(
            lambda response: (
                response.url.endswith(f"/api/knowledge/{article['id']}/review")
                and response.request.method == "POST"
            )
        ) as publish_info:
            row.get_by_role("button", name="发布").click()
        assert publish_info.value.ok, publish_info.value.text()
        island.locator(".knowledge-filter select").nth(0).select_option("published")
        row = island.locator(".knowledge-article", has_text=title)
        expect(row.locator(".knowledge-status")).to_have_text("已发布")
        expect(page.locator("#toast")).to_be_hidden(timeout=5000)
        page.screenshot(path=ARTIFACTS / "ui-knowledge-island.png", full_page=True)

        # The same view remains usable at a narrow operator viewport.
        page.set_viewport_size({"width": 390, "height": 844})
        expect(row).to_be_visible()
        assert page.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
        )
        page.screenshot(path=ARTIFACTS / "ui-knowledge-island-mobile.png", full_page=True)

        row.get_by_role("button", name="编辑").click()
        editor = island.locator("#knowledgeFormReact")
        expect(editor).to_be_visible()
        editor.scroll_into_view_if_needed()
        editor_box = editor.bounding_box()
        assert editor_box is not None
        assert editor_box["x"] >= 0
        assert editor_box["x"] + editor_box["width"] <= 390
        page.screenshot(path=ARTIFACTS / "ui-knowledge-island-mobile-editor.png", full_page=True)
        island.get_by_role("button", name="关闭编辑器").click()
        row.scroll_into_view_if_needed()
        page.set_viewport_size({"width": 1440, "height": 1000})

        page.on("dialog", lambda dialog: dialog.accept())
        with page.expect_response(
            lambda response: (
                response.url.endswith(f"/api/knowledge/{article['id']}/review")
                and response.request.method == "POST"
            )
        ) as retire_info:
            row.get_by_role("button", name="停用").click()
        assert retire_info.value.ok, retire_info.value.text()
        island.locator(".knowledge-filter select").nth(0).select_option("retired")
        row = island.locator(".knowledge-article", has_text=title)
        expect(row.locator(".knowledge-status")).to_have_text("已停用")

        # Reader invariant in the shell: an operator session sees the island
        # but never writer controls, and issues only read requests.
        reader_context = browser.new_context(viewport={"width": 1100, "height": 800})
        reader_page = reader_context.new_page()
        attach_failure_recorders(
            reader_page, console_errors, page_errors, http_errors, failed_requests
        )
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
        reader_page.add_init_script(
            "window.__TAURI_INTERNALS__ = { invoke: () => Promise.resolve() };"
        )
        reader_page.goto(BASE_URL, wait_until="domcontentloaded")
        reader_page.evaluate("() => window.dispatchEvent(new Event('helix-backend-ready'))")
        reader_page.locator('.nav-item[data-view="knowledge"]').click()
        reader_page.wait_for_selector("#knowledgeReactIsland:not(:empty)", timeout=30000)
        reader_island = reader_page.locator("#knowledgeReactIsland")
        expect(reader_island.locator(".knowledge-article").first).to_be_visible(timeout=30000)
        assert reader_page.locator("#newKnowledgeDraft").is_hidden(), (
            "reader must not see the draft button"
        )
        assert reader_island.get_by_role("button", name="发布").count() == 0, (
            "reader must not see publish actions"
        )
        assert reader_island.get_by_role("button", name="停用").count() == 0, (
            "reader must not see retire actions"
        )
        reader_page.wait_for_timeout(300)
        assert all(
            "POST" not in request and "PUT" not in request and "DELETE" not in request
            for request in reader_requests
        ), reader_requests
        reader_context.close()
        context.close()
        browser.close()

    assert not console_errors, (
        f"Browser console errors: {console_errors}; HTTP errors: {http_errors}; "
        f"failed requests: {failed_requests}"
    )
    assert not page_errors, f"Unhandled page errors: {page_errors}"
    assert not http_errors, f"HTTP errors: {http_errors}"
    assert not failed_requests, f"Failed requests: {failed_requests}"
    print(
        json.dumps(
            {
                "status": "ok",
                "article_id": article.get("id"),
                "desktop": str(ARTIFACTS / "ui-knowledge-island.png"),
                "mobile": str(ARTIFACTS / "ui-knowledge-island-mobile.png"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
