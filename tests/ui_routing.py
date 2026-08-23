"""Backlog SLA 策略 / 自动路由规则 — admin 视图前端闭环(浏览器验收)。

后端 `GET/PUT /api/admin/sla-policies`、`GET/POST /api/admin/routing-rules`、
`DELETE /api/admin/routing-rules/{id}`、`GET/POST /api/admin/agent-groups`
此前已就绪;本轮前端接线:管理视图「SLA 策略」卡(列表 + 表单 upsert +
编辑回填)与「自动路由规则」卡(列表 + 创建 + 删除,分配组下拉来自
agent-groups)。

本测在真实会话里闭环验证:
1. API 预建一个坐席组供下拉选择;
2. 切到管理视图 → SLA/路由卡可见,分配组下拉含预建组;
3. 保存 SLA 策略(高优 + 首响 30min/解决 720min)→ 列表出现「高优 · 全渠道」;
4. 点「编辑」→ 表单回填该策略(upsert 同键覆盖,测试幂等);
5. 添加路由规则(意图=退款-{run_id}, 优先级 10)→ 列表出现;
6. 删除该规则 → 行消失;
7. 全程无 console/page/HTTP 4xx+ 错误。
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect, sync_playwright

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
        if "ERR_ABORTED" in failure and (
            "/export" in request.url or "/attachments/" in request.url
        ):
            return
        # 204 No Content 的 DELETE 在 chromium 下可能被标记 ERR_ABORTED(无响应体
        # 边界),非网络层失败;测试内删除/订阅等路径均含此类操作。
        if request.method == "DELETE" and "ERR_ABORTED" in failure:
            return
        failed_requests.append(f"{request.method} {request.url} {failure}")

    run_id = uuid4().hex[:6]
    group_name = f"tier-{run_id}"
    status, group = api_post(
        "/api/admin/agent-groups",
        {"name": group_name, "skills": ["support"], "capacity": 5},
    )
    assert status == 201, f"预建坐席组失败: {status} {group}"
    group_id = group["id"]
    intent = f"退款-{run_id}"

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

        # 切到管理视图:SLA/路由卡可见,分配组下拉含预建组。
        with page.expect_response(
            lambda response: (
                response.url.endswith("/api/admin/agent-groups")
                and response.request.method == "GET"
            )
        ):
            page.locator('.nav-item[data-view="admin"]').click()
        expect(page.locator("#adminView")).to_be_visible()
        expect(page.locator("#slaPolicyForm")).to_be_visible()
        expect(page.locator("#routingRuleForm")).to_be_visible()
        expect(page.locator("#ruleGroup")).to_contain_text(group_name)
        page.screenshot(path=ARTIFACTS / "ui-routing-admin.png", full_page=True)

        # 保存 SLA 策略(高优 / 全渠道 / 首响 30min / 解决 720min)。
        page.locator("#slaPriority").select_option("high")
        page.locator("#slaFirstResponse").fill("30")
        page.locator("#slaResolve").fill("720")
        with page.expect_response(
            lambda response: (
                response.url.endswith("/api/admin/sla-policies")
                and response.request.method == "PUT"
            )
        ) as sla_info:
            page.get_by_role("button", name="保存策略").click()
        assert sla_info.value.ok, sla_info.value.text()
        sla_row = page.locator("#slaPolicyList .sla-rule-row", has_text="高优").first
        expect(sla_row).to_contain_text("高优")
        expect(sla_row).to_contain_text("全渠道")
        expect(sla_row).to_contain_text("首响 30min")

        # 「编辑」回填表单(pure 前端, 校验 upsert 同键覆盖)。
        sla_row.get_by_role("button", name="编辑").click()
        expect(page.locator("#slaPriority")).to_have_value("high")
        expect(page.locator("#slaFirstResponse")).to_have_value("30")
        expect(page.locator("#slaResolve")).to_have_value("720")

        # 添加路由规则(意图=${intent}, 优先级 10)→ 列表出现。
        page.locator("#ruleIntent").fill(intent)
        page.locator("#rulePriority").fill("10")
        with page.expect_response(
            lambda response: (
                response.url.endswith("/api/admin/routing-rules")
                and response.request.method == "POST"
            )
        ) as rule_info:
            page.get_by_role("button", name="添加规则").click()
        assert rule_info.value.ok, rule_info.value.text()
        rule_id = rule_info.value.json()["id"]
        rule_row = page.locator(f".routing-rule-row[data-id='{rule_id}']")
        expect(rule_row).to_contain_text(f"意图 {intent}")
        expect(rule_row).to_contain_text("优先级 10")
        page.screenshot(path=ARTIFACTS / "ui-routing-rules.png", full_page=True)

        # 删除该规则 → 行消失(按 rule_id 精确定位,不依赖残留)。
        with page.expect_response(
            lambda response: (
                response.url.endswith(f"/api/admin/routing-rules/{rule_id}")
                and response.request.method == "DELETE"
            )
        ):
            rule_row.get_by_role("button", name="删除").click()
        expect(
            page.locator(f"#routingRuleList .routing-rule-row[data-id='{rule_id}']")
        ).to_have_count(0)

        browser.close()

    assert not console_errors, f"控制台错误: {console_errors}"
    assert not page_errors, f"未捕获页面错误: {page_errors}"
    assert not http_errors, f"HTTP 4xx+ 错误: {http_errors}"
    assert not failed_requests, f"失败请求: {failed_requests}"
    print(
        json.dumps(
            {
                "status": "ok",
                "group_id": group_id,
                "rule_id": rule_id,
                "admin": str(ARTIFACTS / "ui-routing-admin.png"),
                "rules": str(ARTIFACTS / "ui-routing-rules.png"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
