"""Browser acceptance for the ROADMAP 17.3 customer Web Chat client."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, expect, sync_playwright

from app.widget_token import sign_token


BASE_URL = os.getenv("HELIX_BASE_URL", "http://127.0.0.1:8774").rstrip("/")
WIDGET_SECRET = os.getenv("WIDGET_SECRET", "helix-widget-dev-secret")
ARTIFACTS = Path(__file__).resolve().parents[1] / "artifacts"


def assert_no_overflow(page: Page) -> None:
    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 1, f"widget has {overflow}px horizontal overflow"


def unexpected_request_failures(failures: list[str]) -> list[str]:
    """Chromium labels a normally closed fetch-stream as ERR_ABORTED."""
    return [
        failure
        for failure in failures
        if not (
            failure.startswith(f"GET {BASE_URL}/api/widget/sessions/")
            and "/stream?" in failure
            and "ERR_ABORTED" in failure
        )
    ]


def main() -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    run_id = uuid4().hex[:8]
    token = sign_token(
        secret=WIDGET_SECRET,
        tenant_id="demo",
        customer_ref=f"WIDGET-{run_id}",
        ttl_seconds=1800,
    )
    query = (
        "brand=Northstar+Care&accent=amber&locale=zh&"
        "greeting=%E4%BD%A0%E5%A5%BD%EF%BC%8C%E8%BF%99%E9%87%8C%E6%98%AF%E5%8C%97%E6%98%9F%E6%9C%8D%E5%8A%A1%E5%8F%B0%E3%80%82"
    )
    url = f"{BASE_URL}/widget?{query}#token={token}"
    console_errors: list[str] = []
    page_errors: list[str] = []
    http_errors: list[str] = []
    failed_requests: list[str] = []

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except PlaywrightError:
            browser = playwright.chromium.launch(channel="msedge", headless=True)
        context = browser.new_context(viewport={"width": 360, "height": 760})
        page = context.new_page()
        page.on(
            "console",
            lambda message: (
                console_errors.append(message.text) if message.type == "error" else None
            ),
        )
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.on(
            "response",
            lambda response: (
                http_errors.append(f"{response.request.method} {response.status} {response.url}")
                if response.status >= 400
                else None
            ),
        )
        page.on(
            "requestfailed",
            lambda request: failed_requests.append(
                f"{request.method} {request.url} {request.failure or ''}"
            ),
        )

        document_response = page.goto(url, wait_until="networkidle")
        assert document_response is not None and document_response.ok
        assert "x-frame-options" not in document_response.headers
        assert "frame-ancestors 'self'" in document_response.headers["content-security-policy"]
        expect(page.locator("#widgetBrand")).to_have_text("Northstar Care")
        expect(page.locator("#welcomeCopy")).to_contain_text("北星服务台")
        assert page.locator("body").get_attribute("data-accent") == "amber"
        assert_no_overflow(page)

        page.get_by_label("怎么称呼你？（可选）").fill(f"访客-{run_id}")
        with page.expect_response(
            lambda response: (
                response.url.endswith("/api/widget/sessions") and response.request.method == "POST"
            )
        ) as session_info:
            page.get_by_role("button", name="开始对话").click()
        assert session_info.value.status == 201, session_info.value.text()
        expect(page.locator("#chatView")).to_be_visible()
        expect(page.locator("#prechatView")).to_be_hidden()
        expect(page.locator("#fatalState")).to_be_hidden()
        assert "token=" not in page.url
        stored = page.evaluate("() => JSON.parse(sessionStorage.getItem('helix-widget-session'))")
        assert stored["conversationId"]
        assert stored["token"] != token

        page.get_by_label("发送消息").fill("配送一般多久能到？")
        with page.expect_response(
            lambda response: (
                "/api/widget/sessions/" in response.url
                and "/messages?async_mode=true" in response.url
                and response.request.method == "POST"
            )
        ) as message_info:
            page.get_by_label("发送消息").press("Enter")
        assert message_info.value.ok, message_info.value.text()
        expect(page.locator(".message-row.customer")).to_have_count(1)
        expect(page.locator(".message-row.assistant .message-bubble")).to_contain_text(
            "配送", timeout=20_000
        )
        expect(page.locator("#messageForm")).to_have_attribute("aria-busy", "false")
        expect(page.locator("#connectionBanner")).to_be_hidden()
        expect(page.locator("#prechatView")).to_be_hidden()
        expect(page.locator("#fatalState")).to_be_hidden()
        assert_no_overflow(page)
        page.screenshot(path=ARTIFACTS / "widget-mobile.png", full_page=True)
        assert not unexpected_request_failures(failed_requests), failed_requests

        # A timeout frame is recoverable: the client reconnects to the same
        # canonical job stream, then reconciles against persisted history.
        stream_attempts = 0

        def recover_stream(route) -> None:
            nonlocal stream_attempts
            stream_attempts += 1
            if stream_attempts == 1:
                route.fulfill(
                    status=200,
                    content_type="text/event-stream",
                    body='event: timeout\ndata: {"detail":"test timeout"}\n\n',
                )
            else:
                route.continue_()

        stream_pattern = "**/api/widget/sessions/*/stream?timeout=20"
        page.route(stream_pattern, recover_stream)
        page.get_by_label("发送消息").fill("退货政策是什么？")
        page.get_by_label("发送消息").press("Enter")
        expect(page.locator("#messageForm")).to_have_attribute("aria-busy", "false", timeout=20_000)
        expect(page.locator(".message-row.customer")).to_have_count(2)
        expect(page.locator(".message-row.assistant")).to_have_count(2)
        expect(page.locator("#connectionBanner")).to_be_hidden()
        assert stream_attempts >= 2
        page.unroute(stream_pattern, recover_stream)

        # A just-finished fetch-stream can keep Chromium's connection
        # bookkeeping non-idle after reload even though the new document is
        # ready. Use document readiness plus explicit restored-state checks.
        page.add_init_script(
            """
            const originalFetch = window.fetch.bind(window);
            window.fetch = (input, init) => {
              const url = String(input);
              if (url.includes('/messages?limit=200') && sessionStorage.getItem('widget-delay-history') === '1') {
                sessionStorage.removeItem('widget-delay-history');
                return new Promise((resolve, reject) => {
                  setTimeout(() => originalFetch(input, init).then(resolve, reject), 600);
                });
              }
              return originalFetch(input, init);
            };
            """
        )
        page.evaluate("() => sessionStorage.setItem('widget-delay-history', '1')")
        page.reload(wait_until="domcontentloaded")
        expect(page.locator("#chatView")).to_be_visible()
        expect(page.locator("#connectionBanner")).to_contain_text("正在恢复对话")
        expect(page.locator("#messageForm")).to_have_attribute("aria-busy", "true")
        expect(page.locator("#fatalState")).to_be_hidden()
        expect(page.locator(".message-row.customer")).to_have_count(2)
        expect(page.locator(".message-row.assistant")).to_have_count(2)
        expect(page.locator(".message-row.assistant .message-bubble").first).to_contain_text("配送")
        expect(page.locator("#messageForm")).to_have_attribute("aria-busy", "false")
        expect(page.locator("#connectionBanner")).to_be_hidden()
        assert "token=" not in page.url

        # The server's limited status header is authoritative across refreshes.
        def handoff_history(route) -> None:
            response = route.fetch()
            headers = dict(response.headers)
            headers["x-conversation-status"] = "waiting_human"
            route.fulfill(response=response, headers=headers)

        history_pattern = "**/api/widget/sessions/*/messages?limit=200"
        page.route(history_pattern, handoff_history)
        page.reload(wait_until="domcontentloaded")
        expect(page.locator(".system-note")).to_contain_text("已转交服务团队")
        page.unroute(history_pattern, handoff_history)

        page.set_viewport_size({"width": 900, "height": 900})
        assert_no_overflow(page)
        page.screenshot(path=ARTIFACTS / "widget-desktop.png", full_page=True)

        # Opening an explicit bootstrap link in the same tab starts a new
        # customer entry instead of silently restoring the previous session.
        page.goto(url, wait_until="domcontentloaded")
        expect(page.locator("#prechatView")).to_be_visible()
        expect(page.locator("#chatView")).to_be_hidden()
        assert page.evaluate("() => sessionStorage.getItem('helix-widget-session')") is None
        assert "#token=" in page.url

        # A session-token rejection on the send path must erase the unusable
        # token instead of leaving an interactive but permanently broken chat.
        expired_page = context.new_page()
        expired_page.goto(url, wait_until="domcontentloaded")
        expired_page.get_by_role("button", name="开始对话").click()
        expect(expired_page.locator("#chatView")).to_be_visible()
        expired_page.route(
            "**/api/widget/sessions/*/messages?async_mode=true",
            lambda route: route.fulfill(
                status=401,
                content_type="application/problem+json",
                body='{"detail":"expired"}',
            ),
        )
        expired_page.get_by_label("发送消息").fill("令牌失效测试")
        expired_page.get_by_label("发送消息").press("Enter")
        expect(expired_page.locator("#fatalState")).to_be_visible()
        assert expired_page.evaluate("() => sessionStorage.getItem('helix-widget-session')") is None
        expired_page.close()

        assert not console_errors, console_errors
        assert not page_errors, page_errors
        assert not http_errors, http_errors
        unexpected_failures = unexpected_request_failures(failed_requests)
        assert not unexpected_failures, unexpected_failures
        context.close()
        browser.close()

    print("Web Chat widget browser acceptance passed")


if __name__ == "__main__":
    main()
