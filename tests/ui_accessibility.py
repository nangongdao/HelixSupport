"""ROADMAP section 17.4 accessibility acceptance for operator and widget UIs."""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, Playwright, expect, sync_playwright

from app.widget_token import sign_token


ROOT = Path(__file__).resolve().parents[1]
BASE_URL = os.getenv("HELIX_BASE_URL", "http://127.0.0.1:8765").rstrip("/")
WIDGET_SECRET = os.getenv("WIDGET_SECRET", "helix-widget-dev-secret")
AXE_PATH = ROOT / "node_modules" / "axe-core" / "axe.min.js"


def launch_browser(playwright: Playwright):
    try:
        return playwright.chromium.launch(headless=True)
    except PlaywrightError:
        return playwright.chromium.launch(channel="msedge", headless=True)


def wait_for_operator(page: Page) -> None:
    response = page.goto(BASE_URL, wait_until="domcontentloaded")
    assert response is not None and response.ok
    expect(page.locator("#operatorIdentity")).to_contain_text("demo.admin")
    expect(page.locator("#conversationList")).to_have_attribute("aria-busy", "false")
    page.wait_for_function("() => typeof window.HelixModules?.toggleTheme === 'function'")


def install_axe(page: Page) -> None:
    if page.evaluate("() => typeof window.axe === 'object'"):
        return
    if not AXE_PATH.is_file():
        raise AssertionError(
            f"axe-core is missing at {AXE_PATH}; run `npm ci --ignore-scripts` first"
        )
    page.evaluate(AXE_PATH.read_text(encoding="utf-8"))
    assert page.evaluate("() => typeof window.axe === 'object'")


def assert_no_serious_axe_violations(page: Page, label: str) -> None:
    install_axe(page)
    result = page.evaluate(
        """async () => await window.axe.run(document, {
          resultTypes: ["violations"]
        })"""
    )
    failures = []
    for violation in result["violations"]:
        if violation.get("impact") not in {"critical", "serious"}:
            continue
        failures.append(
            {
                "id": violation["id"],
                "impact": violation["impact"],
                "help": violation["help"],
                "nodes": [
                    {
                        "target": node["target"],
                        "summary": node["failureSummary"],
                    }
                    for node in violation["nodes"]
                ],
            }
        )
    assert not failures, f"{label} axe violations:\n{json.dumps(failures, ensure_ascii=False)}"


def reset_focus(page: Page) -> None:
    page.evaluate(
        """() => {
          const body = document.body;
          body.setAttribute("tabindex", "-1");
          body.focus();
          body.removeAttribute("tabindex");
        }"""
    )


def active_descriptor(page: Page) -> str:
    return page.evaluate(
        """() => {
          const element = document.activeElement;
          if (!(element instanceof HTMLElement)) return "";
          if (element.id) return element.id;
          if (element.dataset.view) return `view:${element.dataset.view}`;
          return `${element.tagName.toLowerCase()}:${
            element.getAttribute("aria-label") || element.textContent.trim().slice(0, 40)
          }`;
        }"""
    )


def assert_desktop_focus_order(page: Page) -> None:
    reset_focus(page)
    sequence = []
    for _ in range(5):
        page.keyboard.press("Tab")
        sequence.append(active_descriptor(page))
    assert sequence == [
        "themeToggle",
        "lowPerfToggle",
        "inspectorToggle",
        "refreshList",
        "view:workspace",
    ], sequence


def assert_visible_focus(page: Page) -> None:
    focus = page.evaluate(
        """() => {
          const style = getComputedStyle(document.activeElement);
          return {
            style: style.outlineStyle,
            width: Number.parseFloat(style.outlineWidth),
          };
        }"""
    )
    assert focus["style"] != "none" and focus["width"] >= 2, focus


def assert_knowledge_keyboard_path(page: Page) -> None:
    knowledge_nav = page.locator('.nav-item[data-view="knowledge"]')
    knowledge_nav.focus()
    page.keyboard.press("Enter")
    expect(page.locator("#knowledgeView")).to_be_visible()
    expect(page.locator("#knowledgeList")).to_have_attribute("aria-busy", "false")

    new_button = page.locator("#newKnowledgeDraft")
    new_button.focus()
    page.keyboard.press("Enter")
    expect(page.locator("#knowledgeEditor")).to_be_visible()
    assert active_descriptor(page) == "knowledgeTitle"
    assert_visible_focus(page)

    page.keyboard.press("Tab")
    assert active_descriptor(page) == "knowledgeContent"
    assert_visible_focus(page)

    close_button = page.locator("#cancelKnowledgeEdit")
    close_button.focus()
    page.keyboard.press("Enter")
    expect(page.locator("#knowledgeEditor")).to_be_hidden()

    page.locator("#knowledgeSearch").focus()
    page.keyboard.press("Tab")
    assert active_descriptor(page) == "knowledgeStatusFilter"
    page.keyboard.press("Tab")
    assert active_descriptor(page) == "knowledgeLanguageFilter"


def assert_mobile_focus_trap(page: Page) -> None:
    wait_for_operator(page)
    trigger = page.locator("#mobileQueue")
    trigger.focus()
    page.keyboard.press("Enter")
    expect(page.locator("#queuePane")).to_have_attribute("aria-modal", "true")
    assert active_descriptor(page) == "queueClose"

    page.keyboard.press("Shift+Tab")
    assert page.evaluate("() => queuePane.contains(document.activeElement)")
    page.keyboard.press("Tab")
    assert page.evaluate("() => queuePane.contains(document.activeElement)"), active_descriptor(
        page
    )

    assert_no_serious_axe_violations(page, "mobile queue drawer")
    page.keyboard.press("Escape")
    expect(page.locator("#queuePane")).not_to_have_attribute("aria-modal", "true")
    assert active_descriptor(page) == "mobileQueue"


def assert_reduced_motion(page: Page, label: str) -> None:
    page.emulate_media(reduced_motion="reduce")
    offenders = page.evaluate(
        """() => {
          const toMs = (value) => Math.max(...value.split(",").map((part) => {
            const item = part.trim();
            if (item.endsWith("ms")) return Number.parseFloat(item);
            if (item.endsWith("s")) return Number.parseFloat(item) * 1000;
            return 0;
          }));
          return [...document.querySelectorAll("*")]
            .map((element) => {
              const style = getComputedStyle(element);
              return {
                element: element.id || element.className || element.tagName,
                animationMs: toMs(style.animationDuration),
                transitionMs: toMs(style.transitionDuration),
              };
            })
            .filter((item) => item.animationMs > 0.02 || item.transitionMs > 0.02)
            .slice(0, 10);
        }"""
    )
    assert not offenders, f"{label} ignores reduced motion: {offenders}"


def widget_url() -> str:
    token = sign_token(
        secret=WIDGET_SECRET,
        tenant_id="demo",
        customer_ref=f"A11Y-{uuid4().hex[:8]}",
        ttl_seconds=1800,
    )
    return f"{BASE_URL}/widget?brand=Northstar+Care&accent=teal&locale=zh#token={token}"


def main() -> None:
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        desktop_context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = desktop_context.new_page()
        wait_for_operator(page)

        assert_desktop_focus_order(page)
        page.evaluate("() => window.HelixModules.applyTheme('dark')")
        # Scan settled theme tokens, not the transient colors during the CSS
        # transition (same settle window as the light scan below).
        page.wait_for_timeout(300)
        assert_no_serious_axe_violations(page, "operator dark theme")
        theme_toggle = page.locator("#themeToggle")
        theme_toggle.focus()
        assert active_descriptor(page) == "themeToggle"
        assert page.evaluate("() => themeToggle.dataset.themeBound") == "true"
        theme_toggle.press("Enter")
        expect(page.locator("html")).to_have_attribute("data-theme", "light")
        # Scan settled theme tokens, not the transient colors during the CSS transition.
        page.wait_for_timeout(300)
        assert_no_serious_axe_violations(page, "operator light theme")

        assert_knowledge_keyboard_path(page)
        assert_no_serious_axe_violations(page, "knowledge view")

        admin_nav = page.locator('.nav-item[data-view="admin"]')
        admin_nav.focus()
        page.keyboard.press("Enter")
        expect(page.locator("#adminView")).to_be_visible()
        expect(page.locator("#quotaReadout")).to_contain_text("租户")
        assert_no_serious_axe_violations(page, "admin view")
        assert_reduced_motion(page, "operator")

        mobile_page = desktop_context.new_page()
        mobile_page.set_viewport_size({"width": 390, "height": 844})
        assert_mobile_focus_trap(mobile_page)

        widget_page = desktop_context.new_page()
        widget_page.set_viewport_size({"width": 360, "height": 760})
        response = widget_page.goto(widget_url(), wait_until="domcontentloaded")
        assert response is not None and response.ok
        expect(widget_page.locator("#prechatView")).to_be_visible()
        assert_no_serious_axe_violations(widget_page, "web chat")
        reset_focus(widget_page)
        widget_page.keyboard.press("Tab")
        assert active_descriptor(widget_page) == "customerName"
        widget_page.keyboard.press("Tab")
        assert active_descriptor(widget_page) == "startButton"
        assert_reduced_motion(widget_page, "web chat")

        browser.close()

    print("Accessibility browser acceptance passed")


if __name__ == "__main__":
    main()
