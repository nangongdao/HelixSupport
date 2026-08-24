"""ROADMAP §18.4 — upward lazy-load of a long message thread (browser smoke).

Verifies that the operator console no longer drags a whole long transcript
into the page at once: opening a conversation renders only the newest
``THREAD_PAGE_LIMIT`` messages plus a "加载更早消息" affordance, and following
that affordance (repeatedly) prepends older pages until the entire transcript
is present — while the view stays anchored where the operator was reading
instead of snapping to the newest message.

Seeding: a conversation with SEED_TURNS customer turns is created through the
public API (each turn also appends an assistant reply), so the transcript is
guaranteed to exceed the tail page.

Server is expected to already run on HELIX_BASE_URL backed by a throwaway
SQLite database (same harness as tests/ui_sserelay.py).

Run:
    python tests/ui_thread_lazy.py
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, sync_playwright

BASE_URL = os.getenv("HELIX_BASE_URL", "http://127.0.0.1:8765").rstrip("/")
API_KEY = os.getenv("HELIX_API_KEY", "helix-demo-key")
# Keep in sync with app/static/app.js.
THREAD_PAGE_LIMIT = int(os.getenv("THREAD_PAGE_LIMIT", "100"))
SEED_TURNS = int(os.getenv("SEED_THREAD_TURNS", "120"))
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


def api_get_detail(path: str) -> list[dict]:
    """GET the full detail JSON and return its messages list."""
    request = urllib.request.Request(f"{BASE_URL}{path}", headers={"X-API-Key": API_KEY})
    with urllib.request.urlopen(request, timeout=20) as response:
        body = json.loads(response.read().decode("utf-8"))
    messages = body.get("messages") if isinstance(body, dict) else body
    return messages if isinstance(messages, list) else []


def instrument(page: Page) -> dict:
    tracker = {
        "console_errors": [],
        "page_errors": [],
        "http_errors": [],
        "failed_requests": [],
    }

    def on_console(message) -> None:
        if message.type == "error":
            tracker["console_errors"].append(f"{message.text} @ {message.location}")

    def on_failed(request) -> None:
        failure = request.failure or ""
        if "ERR_ABORTED" in failure:
            return
        tracker["failed_requests"].append(f"{request.method} {request.url} {failure}")

    page.on("console", on_console)
    page.on("pageerror", lambda error: tracker["page_errors"].append(str(error)))
    page.on("requestfailed", on_failed)
    page.on(
        "response",
        lambda response: (
            tracker["http_errors"].append(
                f"{response.request.method} {response.status} {response.url}"
            )
            if response.status >= 400
            else None
        ),
    )
    return tracker


def wait_row_count(page: Page, count: int, timeout_s: float = 12) -> None:
    """Wait until the thread contains exactly `count` .message-row elements."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if page.locator(".message-row").count() == count:
            return
        page.wait_for_timeout(150)
    raise AssertionError(
        f".message-row count never reached {count} (last={page.locator('.message-row').count()})"
    )


def thread_scroll_top(page: Page, container_height: int | None = None) -> int:
    """scrollTop / (scrollHeight - clientHeight) ratio in percent, or -1."""
    value = page.evaluate(
        """() => {
          const el = document.getElementById("messages");
          const max = el.scrollHeight - el.clientHeight;
          return max <= 0 ? -1 : Math.round((el.scrollTop / max) * 100);
        }"""
    )
    return int(value)


def main() -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    seed_tag = f"thread-{int(time.time())}"

    # Seed a conversation whose transcript outgrows the tail page.
    conversation = api_post("/api/conversations", {"customer_name": seed_tag, "channel": "web"})
    conv_id = conversation["id"]
    for index in range(SEED_TURNS):
        api_post(
            f"/api/conversations/{conv_id}/messages",
            {"content": f"lazy-message-{index:04d}"},
            headers={"Idempotency-Key": f"lazy-{index:04d}-{conv_id[-8:]}"},
        )
    # Expected transcript straight from the API (unlimited detail).
    expected = api_get_detail(f"/api/conversations/{conv_id}")
    expected_total = len(expected)
    expected_first = expected[0]["content"]
    expected_last = expected[-1]["content"]
    assert expected_total > THREAD_PAGE_LIMIT, (
        f"seed produced only {expected_total} messages; need > {THREAD_PAGE_LIMIT}"
    )
    print(
        f"seeded {conv_id}: {expected_total} messages "
        f"(tail page {THREAD_PAGE_LIMIT}, first={expected_first!r}, last={expected_last!r})"
    )

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except PlaywrightError:
            browser = playwright.chromium.launch(channel="msedge", headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        tracker = instrument(page)

        page.goto(BASE_URL)
        # Filter the queue down to exactly the seeded conversation.
        search = page.locator("#searchInput")
        search.fill(seed_tag)
        search.dispatch_event("input")
        item = page.locator(".conversation-item", has_text=seed_tag)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and item.count() == 0:
            page.wait_for_timeout(250)
        assert item.count() > 0, "seeded conversation never appeared in the queue"
        item.click()
        # Tail rendered: exactly THREAD_PAGE_LIMIT rows + the load-older button.
        wait_row_count(page, THREAD_PAGE_LIMIT)
        button = page.locator(".thread-load-older-btn")
        assert button.count() == 1, "load-older affordance missing on a long thread"
        # The newest message is in view at the bottom.
        last_bubble = page.locator(".message-row:last-child .message-bubble")
        if expected_last:
            assert expected_last in (last_bubble.text_content() or ""), (
                "newest message not at the bottom of the tail"
            )
        print(
            f"initial render: {THREAD_PAGE_LIMIT} rows, load-older present, "
            f"last={(last_bubble.text_content() or '')[:24]!r}"
        )

        # Walk older pages by clicking the affordance until it disappears.
        # A click that yields no new rows is the exhaustion signal — the last
        # page may be partial (X-Has-More false) and the affordance dissolves.
        hops = 0
        seen = THREAD_PAGE_LIMIT
        while button.count() == 1 and hops < 12:
            button.click()
            deadline = time.monotonic() + 10
            exhausted_hop = False
            while time.monotonic() < deadline:
                count = page.locator(".message-row").count()
                if count > seen:
                    break
                if page.locator(".thread-load-older-btn").count() == 0:
                    exhausted_hop = count == expected_total
                    break
                page.wait_for_timeout(120)
            if not exhausted_hop:
                assert page.locator(".message-row").count() > seen, (
                    f"hop {hops}: row count did not grow beyond {seen}"
                )
            seen = page.locator(".message-row").count()
            # Operator stays near the top they were reading, never flung to the
            # bottom (scroll 100%). At the literal end of the thread 0% is fine.
            scroll_after = thread_scroll_top(page)
            assert scroll_after < 100, f"hop {hops}: view yanked to bottom ({scroll_after}%)"
            hops += 1
            button = page.locator(".thread-load-older-btn")
            print(f"hop {hops}: rows={seen}, scroll={scroll_after}%")
            if exhausted_hop:
                break

        assert hops >= 1, "lazy-load never engaged"
        assert button.count() == 0, "affordance vanished before the transcript was exhausted"
        assert seen == expected_total, (
            f"exhausted transcript has {seen} rows, expected {expected_total}"
        )
        first_bubble = page.locator(".message-row:first-child .message-bubble")
        assert expected_first in (first_bubble.text_content() or ""), (
            "oldest message not reachable after lazy-loading the whole thread"
        )
        scroll_pct = thread_scroll_top(page)
        assert scroll_pct < 100, f"view yanked to bottom after exhaustion ({scroll_pct}%)"

        page.screenshot(path=ARTIFACTS / "ui-thread-lazy.png")
        browser.close()

    assert not tracker["console_errors"], f"console errors: {tracker['console_errors']}"
    assert not tracker["page_errors"], f"page errors: {tracker['page_errors']}"
    assert not tracker["http_errors"], f"HTTP errors: {tracker['http_errors']}"
    assert not tracker["failed_requests"], f"failed requests: {tracker['failed_requests']}"
    print(
        json.dumps(
            {
                "status": "ok",
                "conversation": conv_id,
                "expected_total_messages": expected_total,
                "tail_page": THREAD_PAGE_LIMIT,
                "lazy_hops": hops,
                "screenshot": str(ARTIFACTS / "ui-thread-lazy.png"),
            }
        )
    )


if __name__ == "__main__":
    main()
