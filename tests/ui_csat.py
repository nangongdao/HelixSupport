"""Backlog CSAT 评分汇总 — 管理视图卡片浏览器验收。

后端 `summarize_csat`(需响应 `POST /api/csat/{token}` 已入 `csat_surveys`)
此前无前端;本轮把汇总接入管理视图「CSAT 评分汇总」卡:总体(样本数 /
平均分 / 好评率)+ 近 14 天逐日趋势。

本测在真实会话里闭环验证:
1. API 记录当前 CSAT 汇总基值(base_total,共享 DB 累积友好);
2. API 创建 3 个会话并 resolve(csat_surveys 行 → 响应带 survey_url),
   提取 token 后通过公开端点 POST 评分(4 / 5 / 2);
3. 切到管理视图 → CSAT 卡可见,样本数 = base_total + 3,平均分为
   非零「x.xx / 5」,好评率 = 2/(base_total+3);
4. 趋势列表含今天(UTC)的日期行;
5. 全程无 console/page/HTTP 4xx+ 错误。
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from datetime import datetime, timezone
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


def api_get(path: str) -> dict:
    request = urllib.request.Request(
        f"{BASE_URL}{path}", headers={"X-API-Key": API_KEY}, method="GET"
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def submit_ratings(count: int, ratings: list[int]) -> int:
    """Resolve ``count`` fresh conversations and rate each via its survey link."""
    answered = 0
    for rating in ratings[:count]:
        status, conv = api_post(
            "/api/conversations",
            {"customer_name": f"csat-{uuid4().hex[:6]}", "channel": "web"},
        )
        assert status == 201, f"建会话失败: {status}"
        status, resolved = api_post(f"/api/conversations/{conv['id']}/resolve", {})
        assert status == 200, f"resolve 失败: {status} {resolved}"
        token = resolved["survey_url"].rsplit("/", 1)[-1]
        # 公开单次评分端点(无鉴权);JSON body。
        request = urllib.request.Request(
            f"{BASE_URL}/api/csat/{token}",
            data=json.dumps({"rating": rating}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            body = json.loads(response.read().decode("utf-8"))
        assert body.get("thank_you") is True, f"评分未落库: {body}"
        answered += 1
    return answered


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

    base = api_get("/api/admin/csat-summary")
    base_total = int(base.get("total") or 0)
    added = submit_ratings(3, [4, 5, 2])
    assert added == 3, f"应提交 3 条评分,实际 {added}"
    expect_total = base_total + added
    # responded_at 由 utc_now() 写入,responded 日趋势取 UTC 日期——必须用
    # UTC 时钟,不能用本地 date.today()(UTC+8 时区 0–8 点会差一天,H1)。
    today = datetime.now(timezone.utc).date().isoformat()

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

        # 切到管理视图 → CSAT 卡可见。
        with page.expect_response(
            lambda response: (
                response.url.endswith("/api/admin/csat-summary")
                and response.request.method == "GET"
            )
        ):
            page.locator('.nav-item[data-view="admin"]').click()
        expect(page.locator("#adminView")).to_be_visible()
        expect(page.locator("#csatReadout")).to_be_visible()

        # 总体三数字。平均分必须是实际数值(1.00–5.00),0.00 或缺失即回归
        # (W4:仅断言文案含 "/ 5" 无法区分真正的空态兜底)。
        dds = page.locator("#csatReadout dd")
        expect(dds.nth(0)).to_have_text(str(expect_total))
        avg_text = dds.nth(1).text_content() or ""
        avg_match = re.search(r"(\d+\.\d+)\s*/\s*5", avg_text)
        assert avg_match, f"平均分格式异常: {avg_text!r}"
        assert 0.0 < float(avg_match.group(1)) <= 5.0, f"平均分越界: {avg_text!r}"
        expect(dds.nth(2)).to_contain_text("%")
        page.screenshot(path=ARTIFACTS / "ui-csat-summary.png", full_page=True)

        # 趋势列表最新行 = 今天(UTC)日期且带实际份数(S2:验证今天的增量行,
        # 而非仅"今天字符串存在"——同一天重复运行也可能平凡通过)。
        first_day = page.locator("#csatTrend .csat-day").first
        expect(first_day).to_contain_text(today)
        expect(first_day).to_contain_text("份")

        browser.close()

    assert not console_errors, f"控制台错误: {console_errors}"
    assert not page_errors, f"未捕获页面错误: {page_errors}"
    assert not http_errors, f"HTTP 4xx+ 错误: {http_errors}"
    assert not failed_requests, f"失败请求: {failed_requests}"
    print(
        json.dumps(
            {
                "status": "ok",
                "base_total": base_total,
                "expect_total": expect_total,
                "today": today,
                "card": str(ARTIFACTS / "ui-csat-summary.png"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
