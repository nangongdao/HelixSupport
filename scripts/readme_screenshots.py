"""Capture the six README screenshots against the v1.4.0 professional SaaS skin.

Mirrors the interaction paths in tests/ui_smoke.py (conversation + handoff)
and scripts/visual_gate.py (knowledge view, mobile queue, widget), but pins
each capture to docs/assets/screenshots/ at a stable viewport so the README
images reflect the current design (3-tier @layer tokens + motion + desktop
splash overlay) rather than the retired Art Deco palette.

Usage: HELIX_BASE_URL=http://127.0.0.1:8766 python scripts/readme_screenshots.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BASE_URL = os.getenv("HELIX_BASE_URL", "http://127.0.0.1:8766").rstrip("/")
WIDGET_SECRET = os.getenv("WIDGET_SECRET", "helix-widget-dev-secret")
OUT = ROOT / "docs" / "assets" / "screenshots"


def launch(playwright):
    try:
        return playwright.chromium.launch(headless=True)
    except PlaywrightError:
        return playwright.chromium.launch(channel="msedge", headless=True)


def wait_for_operator(page: Page) -> None:
    page.goto(BASE_URL, wait_until="domcontentloaded")
    expect(page.locator("#operatorIdentity")).to_contain_text("demo.admin")
    expect(page.locator("#conversationList")).to_have_attribute("aria-busy", "false")


def open_new_conversation(page: Page, name: str, customer_ref: str = "") -> None:
    page.get_by_role("button", name="新建").click()
    expect(page.get_by_role("heading", name="新建会话")).to_be_visible()
    page.get_by_label("客户名称").fill(name)
    if customer_ref:
        page.get_by_label("客户身份标识 可选").fill(customer_ref)
    with page.expect_response(
        lambda r: r.url.endswith("/api/conversations") and r.request.method == "POST"
    ):
        page.get_by_role("button", name="创建会话").click()
    expect(page.get_by_role("heading", name=name)).to_be_visible()


def send_customer_message(page: Page, message: str) -> None:
    page.get_by_label("客户消息", exact=True).fill(message)
    with page.expect_response(
        lambda r: "/api/conversations/" in r.url and r.url.endswith("/messages")
    ):
        page.get_by_role("button", name="发送客户消息").click()


def widget_url() -> str:
    from app.widget_token import sign_token

    token = sign_token(
        secret=WIDGET_SECRET,
        tenant_id="demo",
        customer_ref=f"VIS-{uuid4().hex[:8]}",
        ttl_seconds=1800,
    )
    return f"{BASE_URL}/widget?brand=Northstar+Care&accent=teal&locale=zh#token={token}"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    run_id = uuid4().hex[:6]
    with sync_playwright() as playwright:
        browser = launch(playwright)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        # 1) operator-workspace — dark theme, conversation + evidence panel.
        wait_for_operator(page)
        open_new_conversation(page, f"演示会话-{run_id}", "CUST-1001")
        page.get_by_role("button", name="折叠检查器").click()
        page.get_by_role("button", name="展开检查器").click()
        send_customer_message(page, "配送一般多久能到？")
        expect(page.locator("#messages")).to_contain_text("根据当前服务政策")
        page.get_by_role("tab", name="证据").click()
        expect(page.locator("#inspectorEvidence")).to_contain_text("配送时效")
        page.wait_for_timeout(400)
        page.screenshot(path=OUT / "operator-workspace.png", full_page=False)
        print("captured operator-workspace.png")

        # 2) operator-handoff — human takeover with summary banner + audit tab.
        open_new_conversation(page, f"人工接管-{run_id}")
        send_customer_message(page, "我要退款并投诉，请转人工")
        expect(page.locator("#conversationStatus")).to_have_text("等待人工")
        with page.expect_response(
            lambda r: r.url.endswith("/accept") and r.request.method == "POST"
        ):
            page.get_by_role("button", name="接入", exact=True).click()
        expect(page.locator("#conversationStatus")).to_have_text("人工处理中")
        page.get_by_label("人工回复", exact=True).fill("已接入，正在核验退款条件。")
        page.get_by_role("button", name="发送人工回复", exact=True).click()
        page.get_by_role("button", name="解决", exact=True).click()
        expect(page.locator("#conversationStatus")).to_have_text("已解决")
        page.get_by_role("tab", name="审计").click()
        expect(page.locator("#inspectorAudit")).to_be_visible()
        page.wait_for_timeout(400)
        page.screenshot(path=OUT / "operator-handoff.png", full_page=False)
        print("captured operator-handoff.png")

        # 3) quality-dashboard — supervisor quality view with trend chart.
        page.get_by_role("button", name="关闭低配模式")
        page.locator('.nav-item[data-view="quality"]').click()
        page.wait_for_selector("#qualityView[aria-busy='false'], #qualityViewBuckets", timeout=15000)
        page.wait_for_timeout(600)
        page.screenshot(path=OUT / "quality-dashboard.png", full_page=False)
        print("captured quality-dashboard.png")

        # 4) knowledge-operations — knowledge list + editor.
        page.locator('.nav-item[data-view="knowledge"]').click()
        page.wait_for_selector("#knowledgeList[aria-busy='false']", timeout=15000)
        page.wait_for_timeout(500)
        page.screenshot(path=OUT / "knowledge-operations.png", full_page=False)
        print("captured knowledge-operations.png")

        # 5) tenant-admin — admin cards (quota, members, webhooks, CSAT).
        page.locator('.nav-item[data-view="admin"]').click()
        expect(page.locator("#adminView")).to_be_visible()
        page.wait_for_timeout(500)
        page.screenshot(path=OUT / "tenant-admin.png", full_page=False)
        print("captured tenant-admin.png")

        # 6) web-chat-mobile — embedded Web Chat at mobile width.
        mobile = context.new_page()
        mobile.set_viewport_size({"width": 390, "height": 844})
        mobile.goto(widget_url(), wait_until="domcontentloaded")
        expect(mobile.locator("#welcomeTitle")).to_be_visible()
        mobile.wait_for_timeout(400)
        mobile.screenshot(path=OUT / "web-chat-mobile.png", full_page=False)
        print("captured web-chat-mobile.png")

        browser.close()
    print("all README screenshots captured")
    return 0


if __name__ == "__main__":
    sys.exit(main())
