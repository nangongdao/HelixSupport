"""Console core journey in the DESKTOP SHELL (islands mounted).

ui_smoke.py drives the same journey in web mode against the legacy
renderer; this variant boots the shell (Tauri preconditions before goto,
backend-ready after) so the queue/thread/composer/inspector islands own
the DOM, and proves the operator loop against the real backend there.
Assertions referencing yielded legacy ids are kept where the islands
render into the legacy containers (thread -> #messages, inspector tabs,
queue items).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from time import monotonic
from uuid import uuid4

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, expect, sync_playwright

BASE_URL = os.getenv("HELIX_BASE_URL", "http://127.0.0.1:8765").rstrip("/")
ARTIFACTS = Path(__file__).resolve().parents[1] / "artifacts"


def open_new_conversation(page: Page, name: str, customer_ref: str = "") -> None:
    dialog = page.locator("#conversationDialogReactIsland")
    page.get_by_role("button", name="新建").click()
    expect(dialog.get_by_role("heading", name="新建会话")).to_be_visible()
    dialog.get_by_label("客户名称").fill(name)
    if customer_ref:
        dialog.get_by_label("客户身份标识 可选").fill(customer_ref)
    with page.expect_response(
        lambda response: (
            response.url.endswith("/api/conversations") and response.request.method == "POST"
        )
    ) as response_info:
        dialog.get_by_role("button", name="创建会话").click()
    response = response_info.value
    assert response.ok, f"Conversation creation failed: {response.status} {response.text()}"
    expect(dialog.get_by_role("heading", name="新建会话")).not_to_be_visible()
    expect(page.locator("#conversationTitle")).to_have_text(name)


def send_customer_message(page: Page, message: str) -> None:
    composer = page.locator("#composerReactIsland")
    composer.get_by_label("客户消息", exact=True).fill(message)
    with page.expect_response(
        lambda response: (
            "/api/conversations/" in response.url
            and response.url.endswith("/messages")
            and response.request.method == "POST"
        )
    ) as response_info:
        composer.get_by_role("button", name="发送客户消息").click()
    response = response_info.value
    assert response.ok, f"Customer message failed: {response.status} {response.text()}"


def main() -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    console_errors: list[str] = []
    page_errors: list[str] = []
    http_errors: list[str] = []
    failed_requests: list[str] = []
    feedback_events: list[str] = []

    def record_console(message) -> None:
        if message.type == "error":
            console_errors.append(f"{message.text} @ {message.location}")

    def record_failed_request(request) -> None:
        failure = request.failure or ""
        if request.url.startswith(f"{BASE_URL}/api/events/queue") and "ERR_ABORTED" in failure:
            return
        failed_requests.append(f"{request.method} {request.url} {failure}")

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except PlaywrightError:
            browser = playwright.chromium.launch(channel="msedge", headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        page.on("console", record_console)
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.on("requestfailed", record_failed_request)
        page.on(
            "request",
            lambda request: (
                feedback_events.append(f"request:{request.method}")
                if request.url.endswith("/feedback")
                else None
            ),
        )
        page.on(
            "response",
            lambda response: (
                feedback_events.append(f"response:{response.status}")
                if response.url.endswith("/feedback")
                else None
            ),
        )
        page.on(
            "response",
            lambda response: (
                http_errors.append(f"{response.request.method} {response.status} {response.url}")
                if response.status >= 400
                else None
            ),
        )
        # Desktop-shell boot: islands mount only when the Tauri preconditions
        # exist before goto; the backend-ready event releases the shell. The
        # identity readout is island-rendered here, so wait via text.
        page.add_init_script("window.__TAURI_INTERNALS__ = { invoke: () => Promise.resolve() };")
        page.goto(BASE_URL, wait_until="domcontentloaded")
        page.evaluate("() => window.dispatchEvent(new Event('helix-backend-ready'))")
        expect(page.get_by_text("demo.admin", exact=False).first).to_be_visible(timeout=30000)
        expect(page.get_by_role("heading", name="会话队列")).to_be_visible()

        # Low-perf probes depending on the runner's core count: 4-core CI
        # hosts auto-enable it (detectConstrainedDevice), dev machines do
        # not. Normalise to the "off" state first so the toggle exercises
        # both directions deterministically.
        if page.get_by_role("button", name="关闭低配模式").count():
            page.get_by_role("button", name="关闭低配模式").click()
            expect(page.locator("#perfHint")).to_be_hidden()

        with page.expect_response(
            lambda response: "/api/conversations?" in response.url and "limit=20" in response.url
        ):
            page.get_by_role("button", name="开启低配模式").click()
        # §17.2 三档密度: low-perf forces ``data-density="compact"`` (the
        # legacy is-compact class no longer exists since the density upgrade).
        assert page.locator("body").evaluate(
            "element => element.getAttribute('data-density') === 'compact' && element.classList.contains('is-low-perf')"
        )
        expect(page.locator("#perfHint")).to_be_visible()
        expect(page.locator("#liveStatus")).to_have_text("POLL")
        page.get_by_role("button", name="关闭低配模式").click()
        expect(page.locator("#perfHint")).to_be_hidden()

        run_id = uuid4().hex[:6]
        open_new_conversation(page, f"浏览器验收-{run_id}", "CUST-1001")

        inspector = page.locator("#inspectorReactIsland")
        page.get_by_role("button", name="折叠检查器").click()
        expect(inspector.locator(".inspector-surface")).to_be_hidden()
        expect(page.get_by_role("button", name="展开检查器")).to_be_visible()
        page.get_by_role("button", name="展开检查器").click()
        expect(inspector.locator(".inspector-surface")).to_be_visible()

        # Islands own these surfaces: thread renders into #threadReactIsland
        # (legacy #messages is yielded/hidden), the inspector tabs and the
        # note composer render inside #inspectorReactIsland.
        thread = page.locator("#threadReactIsland")
        send_customer_message(page, "配送一般多久能到？")
        expect(thread).to_contain_text("根据当前服务政策")
        expect(page.locator("#composerFormReact")).to_have_attribute("aria-busy", "false")
        expect(inspector).to_contain_text("knowledge")
        inspector.get_by_role("tab", name="证据").click()
        expect(inspector.get_by_role("tabpanel")).to_contain_text("配送时效")
        expect(inspector.locator(".citation-item")).to_have_count(1)
        feedback_button = thread.locator(".feedback-button[data-feedback='1']").last
        feedback_button.click(position={"x": 2, "y": 2})
        feedback_deadline = monotonic() + 20
        while feedback_button.get_attribute("aria-pressed") != "true":
            if monotonic() >= feedback_deadline:
                raise AssertionError(
                    "Feedback did not reach its recorded UI state: "
                    f"events={feedback_events}, enabled={feedback_button.is_enabled()}, "
                    f"toast={page.locator('#toast').text_content()!r}, "
                    f"console={console_errors}, page_errors={page_errors}, "
                    f"http_errors={http_errors}, failed_requests={failed_requests}"
                )
            page.wait_for_timeout(250)
        inspector.get_by_role("tab", name="概览").click()
        priority_high = inspector.locator(".priority-option[data-priority='high']")
        priority_high.click()
        expect(priority_high).to_have_attribute("aria-pressed", "true")
        browser_label = f"browser-{run_id}"
        labels_input = inspector.locator("#conversationLabelsForm input[name='labels']")
        labels_input.fill(f"{browser_label}, priority")
        with page.expect_response(
            lambda response: response.url.endswith("/labels") and response.request.method == "PUT"
        ) as label_response_info:
            page.get_by_role("button", name="保存标签").click()
        assert label_response_info.value.ok, label_response_info.value.text()
        expect(inspector.locator(".label-chip", has_text=browser_label)).to_have_count(1)
        with page.expect_response(
            lambda response: (
                "/api/conversations?" in response.url and f"label={browser_label}" in response.url
            )
        ):
            page.locator("#labelFilter").select_option(browser_label)
        expect(page.locator("#queueReactIsland .conversation-item")).to_have_count(1)
        with page.expect_response(
            lambda response: "/api/conversations?" in response.url and "label=" not in response.url
        ):
            page.locator("#labelFilter").select_option("")
        inspector.locator("#noteInput").fill("Browser verification note")
        inspector.locator("#noteForm button[type='submit']").click()
        expect(thread).to_contain_text("Browser verification note")
        page.screenshot(path=ARTIFACTS / "ui-console-island.png", full_page=True)

        open_new_conversation(page, f"人工接管-{run_id}")
        send_customer_message(page, "我要退款并投诉，请转人工")
        expect(page.locator("#conversationStatus")).to_have_text("等待人工")
        with page.expect_response(
            lambda response: response.url.endswith("/accept") and response.request.method == "POST"
        ) as accept_response_info:
            page.get_by_role("button", name="接入", exact=True).click()
        assert accept_response_info.value.ok, accept_response_info.value.text()
        expect(page.locator("#conversationStatus")).to_have_text("人工处理中")
        thread = page.locator("#threadReactIsland")
        composer = page.locator("#composerReactIsland")
        inspector = page.locator("#inspectorReactIsland")
        expect(composer.locator("#operatorFormReact")).to_be_visible()
        composer.get_by_label("人工回复", exact=True).fill("已接入，正在核验退款条件。")
        composer.get_by_role("button", name="发送人工回复", exact=True).click()
        expect(thread).to_contain_text("已接入，正在核验退款条件。")
        page.get_by_role("button", name="解决", exact=True).click()
        expect(page.locator("#conversationStatus")).to_have_text("已解决")
        expect(page.get_by_role("button", name="重开", exact=True)).to_be_visible()
        checkboxes = page.locator("#queueReactIsland .conversation-checkbox")
        assert checkboxes.count() >= 2
        checkboxes.nth(0).check()
        checkboxes.nth(1).check()
        queue_bulk = page.locator("#queueReactIsland")
        # The island's toolbar carries classes only (the yielded legacy bar
        # keeps the #bulkToolbar id): scope by .bulk-toolbar and aria-label.
        island_bar = queue_bulk.locator(".bulk-toolbar")
        expect(island_bar).to_be_visible()
        island_bar.locator("select").select_option("priority-normal")
        with page.expect_response(
            lambda response: (
                response.url.endswith("/api/conversations/bulk-actions")
                and response.request.method == "POST"
            )
        ) as bulk_response_info:
            queue_bulk.get_by_role("button", name="应用批量操作").click()
        bulk_response = bulk_response_info.value
        assert bulk_response.ok, bulk_response.text()
        assert bulk_response.json()["updated"] >= 1, bulk_response.json()
        expect(island_bar).not_to_be_visible()
        expect(inspector.locator(".priority-option.normal")).to_have_attribute(
            "aria-pressed", "true"
        )
        page.screenshot(path=ARTIFACTS / "ui-console-island-handoff.png", full_page=True)

        mobile = context.new_page()
        mobile.set_viewport_size({"width": 390, "height": 844})
        mobile.on("console", record_console)
        mobile.on("pageerror", lambda error: page_errors.append(str(error)))
        mobile.on("requestfailed", record_failed_request)
        mobile.on(
            "response",
            lambda response: (
                http_errors.append(f"{response.request.method} {response.status} {response.url}")
                if response.status >= 400
                else None
            ),
        )
        mobile.add_init_script("window.__TAURI_INTERNALS__ = { invoke: () => Promise.resolve() };")
        mobile.goto(BASE_URL, wait_until="domcontentloaded")
        mobile.evaluate("() => window.dispatchEvent(new Event('helix-backend-ready'))")
        expect(mobile.get_by_text("demo.admin", exact=False).first).to_be_visible(timeout=30000)
        expect(mobile.get_by_role("button", name="打开会话队列")).to_be_visible()
        mobile.get_by_role("button", name="打开会话队列").click()
        expect(mobile.locator("#queuePane")).to_have_class("queue-pane is-open")
        expect(mobile.locator("#queuePane")).to_have_css("transform", "matrix(1, 0, 0, 1, 0, 0)")
        expect(mobile.get_by_role("heading", name="会话队列")).to_be_visible()
        expect(mobile.locator(".queue-scrim")).to_be_visible()
        expect(mobile.locator("#queuePane")).to_have_attribute("aria-modal", "true")
        assert mobile.locator(".conversation-pane").get_attribute("inert") is not None
        queue_box = mobile.locator("#queuePane").bounding_box()
        assert queue_box is not None
        assert queue_box["x"] >= -1 and queue_box["width"] >= 340, queue_box
        body_overflow = mobile.evaluate(
            "document.documentElement.scrollWidth > document.documentElement.clientWidth"
        )
        assert not body_overflow, "Mobile viewport has horizontal page overflow"
        mobile.screenshot(path=ARTIFACTS / "ui-console-island-mobile.png", full_page=True)
        mobile.keyboard.press("Escape")
        expect(mobile.locator("#queuePane")).not_to_have_class("queue-pane is-open")
        expect(mobile.locator(".queue-scrim")).to_be_hidden()
        assert mobile.locator(".conversation-pane").get_attribute("inert") is None

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
                "desktop": str(ARTIFACTS / "ui-console-island.png"),
                "handoff": str(ARTIFACTS / "ui-console-island-handoff.png"),
                "mobile": str(ARTIFACTS / "ui-console-island-mobile.png"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
