"""Backlog 多语言客服 — 语言切换/翻译动作 浏览器验收。

后端 M19 已具备自动语言检测(``conversations.language`` 由 orchestrator 写入)
与 ``app/language.py`` 翻译服务,但缺人工操作面。本轮补:
- 会话头「语言」下拉:手动覆盖自动检测(PATCH language,选「自动」恢复 null);
- 每条客户消息的「翻译」工具条:按目标语言 POST translate,渲染结果行。

本测在真实会话里闭环验证:
1. API 预建 open 会话并写入两条客户消息,探针 POST translate 确认降级语义
   (无模型配置时 ``was_translated=false`` / ``source="rule"``,与部署无关);
2. 工作台选中会话 → 语言 picker 默认「自动」→ 改选 ``en`` → PATCH 成功,
   头部 select 与 subtitle 同步为 English;改回「自动」→ PATCH null 清除;
3. 第一条客户消息行的翻译条把目标改 ``zh`` → 点「翻译」→ POST translate 成功,
   结果按探针布尔路径渲染(原文回显通知 或 译文行);
4. 全程无 console/page/HTTP 4xx+ 错误;结束 resolve 会话出队列。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, expect, sync_playwright

BASE_URL = os.getenv("HELIX_BASE_URL", "http://127.0.0.1:8765").rstrip("/")
API_KEY = os.getenv("HELIX_API_KEY", "helix-demo-key")
ARTIFACTS = Path(__file__).resolve().parents[1] / "artifacts"


def api_post(path: str, body: dict, headers: dict | None = None) -> dict:
    request_headers = {"Content-Type": "application/json", "X-API-Key": API_KEY}
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"POST {path}: HTTP {error.code} {error.read()[:200]!r}") from error


def api_get(path: str) -> dict:
    request = urllib.request.Request(f"{BASE_URL}{path}", headers={"X-API-Key": API_KEY})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def select_conversation(page: Page, customer: str) -> None:
    """Click the queue row whose customer name matches (independent of order)."""
    row = page.locator(".conversation-row", has_text=customer).first
    row.click()
    expect(page.locator("#conversationTitle")).to_contain_text(customer)
    expect(page.locator("#conversationView")).to_be_visible()


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
    customer = f"lang-{run_id}"

    # 1) 预建会话 + 两条客户消息 + 探针翻译降级语义。
    conv = api_post("/api/conversations", {"customer_name": customer, "channel": "web"})
    conv_id = conv["id"]
    for index in range(2):
        # Pure CJK (+ digits, which carry no script) so detection is
        # deterministically zh — a latin "message-N" prefix plus ASCII hex in
        # run_id can tip the script count to en and flake the assertion.
        api_post(
            f"/api/conversations/{conv_id}/messages",
            {"content": f"{index}号 你好，请帮我查询订单号"},
            headers={"Idempotency-Key": f"lang-{index}-{run_id}-{conv_id[-8:]}"},
        )
    detail = api_get(f"/api/conversations/{conv_id}")
    customer_messages = [m for m in detail.get("messages", []) if m.get("role") == "customer"]
    assert customer_messages, "预置客户消息未出现在会话详情"
    msg_id = customer_messages[0]["id"]
    probe = api_post(
        f"/api/conversations/{conv_id}/messages/{msg_id}/translate",
        {"target_language": "en"},
    )
    assert "was_translated" in probe and probe.get("source") in {"rule", "model"}, (
        f"翻译探针异常: {probe}"
    )
    rendered_text = "已翻译为 English" if probe["was_translated"] else "当前无翻译模型，已返回原文"

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

        # 2) 语言 picker:默认跟随自动检测 → 手动 en → 恢复「自动」。
        picker = page.locator("#conversationLanguageSelect")
        expect(picker).to_be_visible()
        initial_language = (detail.get("conversation") or {}).get("language") or ""
        expect(picker).to_have_value(initial_language)
        subtitle = page.locator("#conversationSubtitle")
        expect(subtitle).not_to_contain_text("语言:English")

        with page.expect_response(
            lambda response: (
                response.url.endswith(f"/api/conversations/{conv_id}/language")
                and response.request.method == "PATCH"
                and response.ok
            )
        ):
            picker.select_option("en")
        expect(picker).to_have_value("en")
        expect(subtitle).to_contain_text("语言:English")
        page.screenshot(path=ARTIFACTS / "ui-language-override.png", full_page=True)

        with page.expect_response(
            lambda response: (
                response.url.endswith(f"/api/conversations/{conv_id}/language")
                and response.request.method == "PATCH"
                and response.ok
            )
        ):
            picker.select_option("")
        expect(picker).to_have_value("")
        expect(subtitle).not_to_contain_text("语言:English")

        # 3) 消息翻译条:选目标语言 → 翻译 → 结果按探针布尔路径渲染。
        customer_row = page.locator(".message-row.customer").first
        translate_bar = customer_row.locator(".translate-bar")
        expect(translate_bar).to_be_visible()
        translate_bar.locator(".translate-lang").select_option("en")
        with page.expect_response(
            lambda response: (
                "/translate" in response.url and response.request.method == "POST" and response.ok
            )
        ) as translate_info:
            translate_bar.locator(".translate-button").click()
        translate_payload = translate_info.value.json()
        assert translate_payload.get("was_translated") == probe["was_translated"], (
            f"页面翻译与探针语义不一致: {translate_payload}"
        )
        expect(translate_bar.locator(".translate-result")).to_contain_text(rendered_text)
        page.screenshot(path=ARTIFACTS / "ui-language-translate.png", full_page=True)

        browser.close()

    # 测试卫生:resolve 会话出队列,避免 open 会话在共享 DB 累积。
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
                "customer": customer,
                "conv_id": conv_id,
                "translate_was_translated": probe["was_translated"],
                "override": str(ARTIFACTS / "ui-language-override.png"),
                "translate": str(ARTIFACTS / "ui-language-translate.png"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
