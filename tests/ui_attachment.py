"""Backlog 语音/富媒体消息 — 附件下载/预览前端闭环(浏览器验收)。

后端 `GET /api/attachments/{id}/download`(FileResponse、RFC 6266 编码)已
就绪;本轮把消息内附件 chip 从不可点击 ``<span>`` 升级为 ``<a>`` 下载链接,
图片(安全子集 png/jpeg/gif/webp)额外渲染懒加载缩略图预览。

本测在真实会话里闭环验证:
1. 坐席上传 PNG + PDF(set_input_files 触发 change → 自动 POST /api/attachments);
2. 发送人工回复携带 attachment_ids;
3. 消息区 `.attachment-chip` 渲染为下载链接,图片 chip 含缩略图;
4. 缩略图真实加载(naturalWidth > 0,即 download 端点为 img 可服务);
5. 点击非图片 chip 触发 GET …/download 且 200;
6. 全程无 console/page/HTTP 4xx+ 错误。
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from time import monotonic
from uuid import uuid4

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, expect, sync_playwright

BASE_URL = os.getenv("HELIX_BASE_URL", "http://127.0.0.1:8765").rstrip("/")
ARTIFACTS = Path(__file__).resolve().parents[1] / "artifacts"

# 1x1 透明 PNG(magic \x89PNG\r\n\x1a\n,+ 内容自洽 → 可通过 deterministic scan)。
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHQ"
    "gAAAAAAAAAAAAAAAIADQQAAPWdnGQAAAAASUVORK5CYII="
)
# 最小 PDF(%PDF- magic 前缀足以通过扫描;download 只回传字节)。
_PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF"


def open_new_conversation(page: Page, name: str) -> None:
    page.get_by_role("button", name="新建").click()
    expect(page.get_by_role("heading", name="新建会话")).to_be_visible()
    page.get_by_label("客户名称").fill(name)
    with page.expect_response(
        lambda response: (
            response.url.endswith("/api/conversations") and response.request.method == "POST"
        )
    ) as response_info:
        page.get_by_role("button", name="创建会话").click()
    assert response_info.value.ok, f"会话创建失败: {response_info.value.status}"
    expect(page.locator("#conversationTitle")).to_have_text(name)


def request_human_handoff(page: Page, message: str) -> None:
    """Send a customer message requesting human, then drive the conversation to
    human_active.  Depending on auto-routing the session may land directly in
    人工处理中, or queue at 等待人工 where 接入 must be clicked."""
    page.get_by_label("客户消息", exact=True).fill(message)
    with page.expect_response(
        lambda response: (
            "/api/conversations/" in response.url
            and response.url.endswith("/messages")
            and response.request.method == "POST"
        )
    ) as response_info:
        page.get_by_role("button", name="发送客户消息").click()
    assert response_info.value.ok, f"客户消息失败: {response_info.value.status}"
    status = page.locator("#conversationStatus")
    deadline = monotonic() + 20
    while monotonic() < deadline:
        text = status.text_content() or ""
        if "人工处理中" in text:
            return
        if "等待人工" in text:
            with page.expect_response(
                lambda response: (
                    response.url.endswith("/accept") and response.request.method == "POST"
                )
            ) as accept_info:
                page.get_by_role("button", name="接入", exact=True).click()
            assert accept_info.value.ok, f"接入失败: {accept_info.value.status}"
            return
        if "已解决" in text or "自动处理中" in text:
            page.wait_for_timeout(300)
    raise AssertionError(f"会话未进入人工处理: status={status.text_content()!r}")


def attach_file(page: Page, *, name: str, mime: str, data: bytes) -> None:
    """Upload one file through the pending-attachment bar (change → auto POST)."""
    with page.expect_response(
        lambda response: (
            response.url.endswith("/api/attachments") and response.request.method == "POST"
        )
    ) as response_info:
        page.locator("#attachmentFile").set_input_files(
            {"name": name, "mimeType": mime, "buffer": data}
        )
    assert response_info.value.ok, (
        f"上传失败: {response_info.value.status} {response_info.value.text()}"
    )


def main() -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    console_errors: list[str] = []
    page_errors: list[str] = []
    http_errors: list[str] = []
    failed_requests: list[str] = []
    download_responses: list[str] = []

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
            "response",
            lambda response: (
                http_errors.append(f"{response.request.method} {response.status} {response.url}")
                if response.status >= 400
                else None
            ),
        )
        page.on(
            "response",
            lambda response: (
                download_responses.append(f"{response.status}")
                if response.url.endswith("/download")
                else None
            ),
        )
        page.goto(BASE_URL)
        expect(page.locator("#operatorIdentity")).to_contain_text("demo.admin")
        expect(page.get_by_role("heading", name="会话队列")).to_be_visible()

        run_id = uuid4().hex[:6]
        open_new_conversation(page, f"附件验收-{run_id}")

        # 客户消息请求转人工 → 接入后上传附件(pending chip 展示)。
        request_human_handoff(page, "附件验收：请人工介入，我稍后上传文件。")
        attach_file(page, name="chart.png", mime="image/png", data=_PNG)
        attach_file(page, name="report.pdf", mime="application/pdf", data=_PDF)
        pending = page.locator(".pending-attachment-chip")
        expect(pending).to_have_count(2)
        expect(pending.first).to_contain_text("chart.png")
        expect(pending.nth(1)).to_contain_text("report.pdf")

        # 发送人工回复,附带两个上传附件。
        with page.expect_response(
            lambda response: (
                response.url.endswith("/operator-messages") and response.request.method == "POST"
            )
        ) as reply_info:
            page.get_by_label("人工回复", exact=True).fill("已附上图片与报告。")
            page.get_by_role("button", name="发送人工回复", exact=True).click()
        assert reply_info.value.ok, f"发送失败: {reply_info.value.status}"
        payload = reply_info.value.request.post_data_json
        assert payload and len(payload.get("attachment_ids") or []) == 2, payload
        expect(page.locator("#pendingAttachments")).not_to_contain_text("chart.png")

        # 消息区附件 chip 是下载链接;图片 chip 含缩略图(至少两附件,图片/非图片各一)。
        chips = page.locator(".message-attachments .attachment-chip")
        expect(chips.first).to_be_visible()
        assert chips.count() >= 2, chips.count()
        image_chip = page.locator(".message-attachments .attachment-chip.is-image")
        expect(image_chip.first).to_be_visible()
        image_link = image_chip.first
        assert (image_link.get_attribute("href", timeout=5000) or "").endswith("/download")
        pdf_chip = page.locator(".message-attachments .attachment-chip:not(.is-image)").first
        expect(pdf_chip).to_be_visible()
        assert (pdf_chip.get_attribute("href") or "").endswith("/download")

        # 缩略图真实加载:download 端点为 img 可服务(demo 会话 cookie 放行)。
        page.wait_for_function(
            "() => { const el = document.querySelector('.attachment-thumb'); "
            "return el !== null && el.complete && el.naturalWidth > 0; }",
            timeout=15000,
        )
        page.screenshot(path=ARTIFACTS / "ui-attachment.png", full_page=True)

        # 点击 PDF chip 应触发真实下载(attachment disposition,context 级事件)。
        with page.expect_download(timeout=15000) as download_info:
            pdf_chip.first.click()
        download = download_info.value
        assert download.failure() is None, download.failure()
        # 图片缩略图加载走 download 端点且 200(PDF 下载已由 expect_download
        # + failure() is None 验证;target=_blank 的下载请求不进当前 page 的
        # response 事件,故不在此计数)。
        assert download_responses.count("200") >= 1, download_responses
        page.screenshot(path=ARTIFACTS / "ui-attachment-download.png", full_page=True)

        browser.close()

    assert not console_errors, f"控制台错误: {console_errors}"
    assert not page_errors, f"未捕获页面错误: {page_errors}"
    assert not http_errors, f"HTTP 4xx+ 错误: {http_errors}"
    assert not failed_requests, f"失败请求: {failed_requests}"
    print(
        json.dumps(
            {
                "status": "ok",
                "attachment": str(ARTIFACTS / "ui-attachment.png"),
                "download": str(ARTIFACTS / "ui-attachment-download.png"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
