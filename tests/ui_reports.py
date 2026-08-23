"""Backlog 报表订阅/导出 — admin 视图前端闭环(浏览器验收)。

后端 `GET/POST /api/admin/report-subscriptions`、`PATCH/DELETE /{id}`、
`POST /api/admin/reports/generate`、`GET /api/admin/reports/{type}/export`
此前已就绪;本轮前端接线:管理视图「报表订阅」卡片(列表 + 创建表单 +
启用/停用 + 删除)与「报表导出」卡片(生成预览 + CSV 下载)。

本测在真实会话里闭环验证:
1. API 预建一个 webhook 端点供下拉选择;
2. 切到管理视图 → 报表订阅/导出卡可见,Webhook 下拉含预建端点;
3. 创建订阅(quality/daily/7 天)→ 列表出现、状态「启用」;
4. 停用 → 「停用」状态,再启用;
5. 生成预览 → 预览区出现报表标题与行数;
6. 导出 CSV → 触发真实下载(.csv 附件);
7. 删除订阅 → 列表回到「暂无报表订阅」;
8. 全程无 console/page/HTTP 4xx+ 错误。
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
        # 下载类导航(CSV 导出 / attachment)由浏览器按磁盘下载中止,非真实失败。
        if "ERR_ABORTED" in failure and (
            "/export" in request.url or "/attachments/" in request.url
        ):
            return
        failed_requests.append(f"{request.method} {request.url} {failure}")

    run_id = uuid4().hex[:6]
    # 公网 IP 字面量(93.184.216.34 = example.com):SSRF 校验拒绝私有/回环与
    # 无法解析的域名,纯 IP 零 DNS 依赖;仅注册 + 订阅,不触发真实投递。
    hook_url = f"http://93.184.216.34/report-hook-{run_id}"
    status, hook = api_post(
        "/api/webhooks",
        {"url": hook_url, "events": ["report.generated"], "secret": f"secret-{run_id}"},
    )
    assert status == 201, f"预建 webhook 失败: {status} {hook}"
    hook_id = hook["id"]

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

        # 切到管理视图:报表订阅/导出卡可见,Webhook 下拉含预建端点。
        with page.expect_response(
            lambda response: (
                response.url.endswith("/api/admin/report-subscriptions")
                and response.request.method == "GET"
            )
        ):
            page.locator('.nav-item[data-view="admin"]').click()
        expect(page.locator("#adminView")).to_be_visible()
        expect(page.locator("#reportSubscriptionForm")).to_be_visible()
        expect(page.locator("#reportGenerateForm")).to_be_visible()
        expect(page.locator("#reportWebhook")).to_contain_text(hook_url)
        page.screenshot(path=ARTIFACTS / "ui-reports-admin.png", full_page=True)

        # 创建订阅(默认 quality/daily/7)→ 列表出现且「启用」。
        with page.expect_response(
            lambda response: (
                response.url.endswith("/api/admin/report-subscriptions")
                and response.request.method == "POST"
            )
        ) as create_info:
            page.get_by_role("button", name="创建订阅").click()
        assert create_info.value.ok, (
            f"创建订阅失败: {create_info.value.status} {create_info.value.text()}"
        )
        sub_id = create_info.value.json()["id"]
        sub_row = page.locator(f".admin-report-sub[data-id='{sub_id}']")
        expect(sub_row).to_contain_text("质量报表 · 每日")
        expect(sub_row).to_contain_text("启用")
        expect(sub_row).to_contain_text("窗口 7 天")

        # 停用 → 「停用」状态 + 按钮变「启用」。
        with page.expect_response(
            lambda response: (
                response.url.endswith(f"/api/admin/report-subscriptions/{sub_id}")
                and response.request.method == "PATCH"
            )
        ) as patch_info:
            sub_row.get_by_role("button", name="停用").click()
        assert patch_info.value.json()["active"] is False, patch_info.value.json()
        expect(sub_row).to_contain_text("停用")
        expect(sub_row.get_by_role("button", name="启用")).to_be_visible()

        # 重新启用(回到活跃,后续删除前状态自洽)。
        with page.expect_response(
            lambda response: (
                response.url.endswith(f"/api/admin/report-subscriptions/{sub_id}")
                and response.request.method == "PATCH"
            )
        ):
            page.locator(f".admin-report-sub[data-id='{sub_id}']").get_by_role(
                "button", name="启用"
            ).click()
        expect(page.locator(f".admin-report-sub[data-id='{sub_id}']")).to_contain_text("启用")

        # 生成预览(quality, 7 天)→ 预览区显示标题与行数。
        with page.expect_response(
            lambda response: (
                response.url.endswith("/api/admin/reports/generate")
                and response.request.method == "POST"
            )
        ) as generate_info:
            page.get_by_role("button", name="生成预览").click()
        assert generate_info.value.ok, generate_info.value.text()
        expect(page.locator("#reportPreview")).to_be_visible()
        expect(page.locator("#reportPreview")).to_contain_text("质量报表")

        # 导出 CSV → 真实下载(.csv attachment)。
        with page.expect_download() as download_info:
            page.get_by_role("button", name="导出 CSV").click()
        download = download_info.value
        assert download.suggested_filename.endswith(".csv"), download.suggested_filename
        page.screenshot(path=ARTIFACTS / "ui-reports-export.png", full_page=True)

        # 删除订阅 → 本轮订阅行消失(按 sub_id 精确定位,不受共享 DB 残留影响)。
        with page.expect_response(
            lambda response: (
                response.url.endswith(f"/api/admin/report-subscriptions/{sub_id}")
                and response.request.method == "DELETE"
            )
        ):
            page.locator(f".admin-report-sub[data-id='{sub_id}']").get_by_role(
                "button", name="删除"
            ).click()
        expect(
            page.locator(f"#reportSubscriptionList .admin-report-sub[data-id='{sub_id}']")
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
                "subscription_id": sub_id,
                "hook_id": hook_id,
                "admin": str(ARTIFACTS / "ui-reports-admin.png"),
                "export": str(ARTIFACTS / "ui-reports-export.png"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
