"""Backlog 工单化 — 列表 / 详情 / 状态机前端闭环(浏览器验收)。

后端 `POST/GET /api/tickets`、`GET/PATCH /api/tickets/{id}`、
`POST /api/tickets/{id}/transition|link` 已就绪;本轮把"仅单向创建"升级为
生命周期 UI:workspace「队列/工单」tab、工单列表(状态过滤)、工单详情
(字段/关联会话/状态机按钮)、transition 状态机(open→in_progress/closed、→
closed、closed→open)、关联当前会话。

本测在真实会话里闭环验证:
1. 打开会话 → 点「转工单」(接受 prompt 主题)→ 工单徽章出现;
2. 切到「工单」tab → 列表含该工单;点击进详情;
3. 状态机:待处理 →「开始处理」→ 处理中 →「关闭」→ 已关闭 →「重开」→ 待处理;
4. 「关联当前会话」把另一个会话挂到本工单,详情关联列表出现;
5. 全程无 console/page/HTTP 4xx+ 错误。
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, expect, sync_playwright

BASE_URL = os.getenv("HELIX_BASE_URL", "http://127.0.0.1:8765").rstrip("/")
ARTIFACTS = Path(__file__).resolve().parents[1] / "artifacts"


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
    conv_id = response_info.value.json()["id"]
    # Wait for the UI to finish selecting the new conversation BEFORE promoting
    # priority.  The newConversationForm submit handler calls loadDetail then
    # refreshAll; if we PATCH mid-flight, the background refresh can re-select
    # a different top row and overwrite the title.
    expect(page.locator("#conversationTitle")).to_have_text(name)
    # Promote to high priority so the seeded conversation stays at the top of
    # the queue regardless of how many open rows the shared scratch DB already
    # holds (load-test seeding promotes rows to high).
    api_patch(f"/api/conversations/{conv_id}", {"priority": "high"})


def api_patch(path: str, body: dict) -> dict:
    """PATCH helper — used to promote a conversation to high priority."""
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-API-Key": "helix-demo-key"},
        method="PATCH",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    console_errors: list[str] = []
    page_errors: list[str] = []
    http_errors: list[str] = []
    failed_requests: list[str] = []

    def record_console(message) -> None:
        if message.type == "error":
            console_errors.append(f"{message.text} @ {message.location}")

    def record_failed_request(request) -> None:
        failure = request.failure or ""
        if request.url.startswith(f"{BASE_URL}/api/events/queue") and "ERR_ABORTED" in failure:
            return
        failed_requests.append(f"{request.method} {request.url} {failure}")

    def on_dialog(dialog) -> None:
        if dialog.type == "prompt":
            dialog.accept("退款单提交失败工单")
        else:
            dialog.dismiss()

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
        page.on("dialog", on_dialog)
        page.on(
            "response",
            lambda response: (
                http_errors.append(f"{response.request.method} {response.status} {response.url}")
                if response.status >= 400
                else None
            ),
        )
        page.goto(BASE_URL)
        expect(page.locator("#operatorIdentity")).to_contain_text("demo.admin")
        expect(page.get_by_role("heading", name="会话队列")).to_be_visible()

        run_id = uuid4().hex[:6]
        open_new_conversation(page, f"工单验收-{run_id}")

        # 打开会话后「转工单」可用 → 创建,徽章出现。
        ticket_button = page.locator("#ticketBtn")
        expect(ticket_button).to_be_visible()
        with page.expect_response(
            lambda response: (
                response.url.endswith("/api/tickets") and response.request.method == "POST"
            )
        ) as create_info:
            ticket_button.click()
        create_response = create_info.value
        assert create_response.ok, f"转工单失败: {create_response.status} {create_response.text()}"
        ticket_id = create_response.json()["id"]
        expect(page.locator("#ticketBadge")).to_be_visible()
        expect(page.locator("#ticketBadge")).to_contain_text(ticket_id)

        # 切到「工单」tab → 列表含该工单。matcher 收紧为列表 URL(/api/tickets
        # 或 ?status= 查询),避免命中 enrichTicketBadge 安排的 GET /api/tickets/{id}。
        with page.expect_response(
            lambda response: (
                bool(re.search(r"/api/tickets(?:\?|$)", response.url))
                and response.request.method == "GET"
            )
        ):
            page.get_by_role("tab", name="工单").click()
        expect(page.locator("#ticketPane")).to_be_visible()
        ticket_row = page.locator(f".ticket-row[data-ticket-id='{ticket_id}']")
        expect(ticket_row).to_contain_text("退款单提交失败工单")
        page.screenshot(path=ARTIFACTS / "ui-ticket-list.png", full_page=True)

        # 进详情:待处理 →「开始处理」。
        with page.expect_response(
            lambda response: (
                response.url.endswith(f"/api/tickets/{ticket_id}")
                and response.request.method == "GET"
            )
        ):
            ticket_row.click()
        expect(page.locator("#ticketDetailView")).to_be_visible()
        expect(page.locator("#ticketDetailStatus")).to_have_text("待处理")
        expect(page.locator("#ticketDetailTitle")).to_contain_text(ticket_id)
        expect(page.get_by_role("button", name="开始处理")).to_be_visible()

        with page.expect_response(
            lambda response: (
                response.url.endswith(f"/api/tickets/{ticket_id}/transition")
                and response.request.method == "POST"
            )
        ) as transition_info:
            page.get_by_role("button", name="开始处理").click()
        assert transition_info.value.ok, transition_info.value.text()
        assert transition_info.value.json()["status"] == "in_progress", transition_info.value.json()
        expect(page.locator("#ticketDetailStatus")).to_have_text("处理中")
        expect(page.get_by_role("button", name="关闭")).to_be_visible()

        # 关闭 → 已关闭 → 重开。
        with page.expect_response(
            lambda response: (
                response.url.endswith(f"/api/tickets/{ticket_id}/transition")
                and response.request.method == "POST"
            )
        ) as close_info:
            page.get_by_role("button", name="关闭").click()
        assert close_info.value.json()["status"] == "closed", close_info.value.json()
        expect(page.locator("#ticketDetailStatus")).to_have_text("已关闭")
        expect(page.get_by_role("button", name="重开")).to_be_visible()
        page.screenshot(path=ARTIFACTS / "ui-ticket-closed.png", full_page=True)

        with page.expect_response(
            lambda response: (
                response.url.endswith(f"/api/tickets/{ticket_id}/transition")
                and response.request.method == "POST"
            )
        ) as reopen_info:
            page.get_by_role("button", name="重开").click()
        assert reopen_info.value.json()["status"] == "open", reopen_info.value.json()
        expect(page.locator("#ticketDetailStatus")).to_have_text("待处理")

        # 切回队列 tab(关闭工单详情)→ 打开第二个会话。
        page.get_by_role("tab", name="队列").click()
        expect(page.locator("#ticketDetailView")).to_be_hidden()
        expect(page.locator("#ticketPane")).to_be_hidden()
        open_new_conversation(page, f"工单关联-{run_id}")

        # 再切工单 tab 进详情:selectedId=第二个会话(未关联)→「关联当前会话」可见。
        page.get_by_role("tab", name="工单").click()
        expect(page.locator("#ticketPane")).to_be_visible()
        with page.expect_response(
            lambda response: (
                response.url.endswith(f"/api/tickets/{ticket_id}")
                and response.request.method == "GET"
            )
        ):
            page.locator(f".ticket-row[data-ticket-id='{ticket_id}']").click()
        expect(page.locator("#ticketDetailView")).to_be_visible()
        expect(page.get_by_role("button", name="关联当前会话")).to_be_visible()
        with page.expect_response(
            lambda response: (
                response.url.endswith(f"/api/tickets/{ticket_id}/link")
                and response.request.method == "POST"
            )
        ) as link_info:
            page.get_by_role("button", name="关联当前会话").click()
        assert link_info.value.ok, f"关联失败: {link_info.value.status} {link_info.value.text()}"
        expect(page.locator("#ticketDetailConvs")).to_contain_text("工单关联")
        expect(page.locator("#ticketDetailConvs .ticket-conv-row")).to_have_count(2)

        # 点详情关联列表中「工单验收」那行 → 跳回队列并打开该会话(回归
        # jumpToTicketConversation:不得因 selectedId 为空抢开队列第一条,
        # 也不得停在详情)。has_text 按客户名锁定行,不依赖后端排序。
        with page.expect_response(
            lambda response: (
                response.url.startswith(f"{BASE_URL}/api/conversations/")
                and response.request.method == "GET"
            )
        ):
            page.locator("#ticketDetailConvs .ticket-conv-row", has_text="工单验收").click()
        expect(page.locator("#ticketDetailView")).to_be_hidden()
        expect(page.locator("#ticketPane")).to_be_hidden()
        expect(page.get_by_role("heading", name="会话队列")).to_be_visible()
        expect(page.locator("#conversationTitle")).to_contain_text("工单验收")

        # 切回队列 tab:工单详情视图被关闭,队列恢复。
        page.get_by_role("tab", name="队列").click()
        expect(page.locator("#ticketDetailView")).to_be_hidden()
        expect(page.locator("#ticketPane")).to_be_hidden()
        expect(page.get_by_role("heading", name="会话队列")).to_be_visible()
        page.screenshot(path=ARTIFACTS / "ui-ticket-detail.png", full_page=True)

        browser.close()

    assert not console_errors, f"控制台错误: {console_errors}"
    assert not page_errors, f"未捕获页面错误: {page_errors}"
    assert not http_errors, f"HTTP 4xx+ 错误: {http_errors}"
    assert not failed_requests, f"失败请求: {failed_requests}"
    print(
        json.dumps(
            {
                "status": "ok",
                "ticket_id": ticket_id,
                "list": str(ARTIFACTS / "ui-ticket-list.png"),
                "closed": str(ARTIFACTS / "ui-ticket-closed.png"),
                "detail": str(ARTIFACTS / "ui-ticket-detail.png"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
