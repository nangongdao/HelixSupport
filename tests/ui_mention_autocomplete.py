"""Backlog @提及 输入自动补全 — note composer 浏览器验收。

后端 M18 已实现 note 内 ``@actor`` 提取与 ``/api/mentions`` 收件箱,本轮补
输入侧 UX:在内部备注输入 ``@`` 时按当前租户成员名单弹出候选坐席,支持前缀
过滤、方向键高亮、Enter/Tab/点击选中(选中后插入 ``@actor_id ``),Escape 取消。

本测在真实会话里闭环验证:
1. API 预建一个协作坐席(``mentor-{run_id}``)与一个 open 会话;
2. 打开工作台选中该会话 → note 输入 ``@`` → 候选列表出现且含预建坐席
   (排除自己 demo.admin);
3. 前缀 ``mentor-{run_id}`` 过滤只剩该坐席;
4. ArrowDown 高亮 + Enter 选中 → 输入框变为 ``@mentor-{run_id} ``;
5. 填正文提交 note → 后端 notes 端点成功;
6. 全程无 console/page/HTTP 4xx+ 错误。
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, expect, sync_playwright

BASE_URL = os.getenv("HELIX_BASE_URL", "http://127.0.0.1:8765").rstrip("/")
API_KEY = os.getenv("HELIX_API_KEY", "helix-demo-key")
ARTIFACTS = Path(__file__).resolve().parents[1] / "artifacts"


def api_post(path: str, body: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-API-Key": API_KEY},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def api_patch(path: str, body: dict) -> dict:
    """PATCH helper — used to promote a conversation to high priority."""
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-API-Key": API_KEY},
        method="PATCH",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def select_conversation(page: Page, customer: str) -> None:
    """Click the queue row whose customer name matches (independent of order)."""
    row = page.locator(".conversation-row", has_text=customer).first
    row.click()
    expect(page.locator("#conversationTitle")).to_contain_text(customer)
    expect(page.locator("#noteForm")).to_be_visible()


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

    run_id = uuid4().hex[:6]
    mentor = f"mentor-{run_id}"
    customer = f"mention-{run_id}"
    status, _ = api_post(
        "/api/admin/tenants/demo/members",
        {"actor_id": mentor, "role": "operator"},
    )
    assert status == 201, f"预建协作坐席失败: {mentor}"
    status, conv = api_post(
        "/api/conversations",
        {"customer_name": customer, "channel": "web"},
    )
    assert status == 201, f"预建会话失败: {customer}"
    conv_id = conv["id"]
    # Promote to high priority so the seeded conversation sorts to the top of
    # the queue regardless of how many open rows the shared scratch DB already
    # holds (load-test seeding promotes rows to high).
    api_patch(f"/api/conversations/{conv_id}", {"priority": "high"})

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
        page.goto(BASE_URL)
        expect(page.locator("#operatorIdentity")).to_contain_text("demo.admin")
        select_conversation(page, customer)
        note_input = page.locator("#noteInput")
        suggest = page.locator("#mentionSuggest")

        # 1) 输入 @ → 候选列表出现且排除自己(不依赖 mentor 落在 top-8:
        # 空前缀时所有成员匹配,共享 DB 累积后排序不可控)。
        note_input.fill("@")
        expect(suggest).to_be_visible()
        expect(suggest).not_to_contain_text("demo.admin")
        page.screenshot(path=ARTIFACTS / "ui-mention-roster.png", full_page=True)

        # 2) 前缀过滤只剩该坐席(精确 actor,不受残留成员影响)。
        note_input.fill(f"@{mentor}")
        expect(suggest).to_contain_text(mentor)
        expect(suggest.locator(".macro-option")).to_have_count(1)

        # 3) ArrowDown 高亮 + Enter 选中 → 插入 `@actor `。
        note_input.press("ArrowDown")
        note_input.press("Enter")
        expect(note_input).to_have_value(f"@{mentor} ")

        # 4) 再输入其它候选前缀(Escape 取消),确保列表收起后恢复键入。
        note_input.fill(f"@{mentor[:6]}a-none")
        expect(suggest).not_to_be_visible()

        # 5) 填正文提交 note → 后端成功。
        note_input.fill(f"@{mentor} 交接:请复核退款工单 {run_id}")
        with page.expect_response(
            lambda response: (
                "/notes" in response.url and response.request.method == "POST" and response.ok
            )
        ) as note_info:
            page.get_by_role("button", name="添加内部备注").click()
        assert note_info.value.ok, note_info.value.text()
        expect(page.locator("#noteForm")).to_be_visible()
        page.screenshot(path=ARTIFACTS / "ui-mention-submitted.png", full_page=True)

        browser.close()

    # 测试卫生:把会话 resolve 出队列,避免 open 会话在共享 DB 累积。
    try:
        api_post(f"/api/conversations/{conv_id}/resolve", {})
    except Exception:
        pass

    assert not console_errors, f"控制台错误: {console_errors}"
    assert not page_errors, f"未捕获页面错误: {page_errors}"
    assert not http_errors, f"HTTP 4xx+ 错误: {http_errors}"
    assert not failed_requests, f"失败请求: {failed_requests}"
    print(
        json.dumps(
            {
                "status": "ok",
                "mentor": mentor,
                "customer": customer,
                "roster": str(ARTIFACTS / "ui-mention-roster.png"),
                "submitted": str(ARTIFACTS / "ui-mention-submitted.png"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
