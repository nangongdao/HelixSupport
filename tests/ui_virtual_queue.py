"""ROADMAP §18.4 — virtual queue smoke in a real browser.

Verifies that once the in-state queue exceeds VIRTUAL_THRESHOLD (200 rows)
the list switches to viewport-window rendering: spacer pads keep the total
scroll height, only the visible band is in the DOM, scrolling re-renders the
window to the deep rows, selection still works, and no console/page errors
surface.

Server is expected to already run on HELIX_BASE_URL backed by a throwaway
SQLite database; the script seeds N=230 conversations up front and paginates
the UI until the queue count reports them all.

Run:
    python tests/ui_virtual_queue.py
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
from playwright.sync_api import Page, expect, sync_playwright

BASE_URL = os.getenv("HELIX_BASE_URL", "http://127.0.0.1:8765").rstrip("/")
API_KEY = os.getenv("HELIX_API_KEY", "helix-demo-key")
SEED_COUNT = int(os.getenv("SEED_CONVERSATIONS", "230"))
# Keep in sync with app/static/js/vqueue.js (guarded by tests there).
VIRTUAL_THRESHOLD = int(os.getenv("VIRTUAL_THRESHOLD", "200"))
ARTIFACTS = Path(__file__).resolve().parents[1] / "artifacts"


def api_post(path: str, body: dict) -> int:
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-API-Key": API_KEY},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def count_conversations() -> int:
    """Total open conversations by walking X-Next-Cursor pages."""
    total = 0
    cursor: str | None = None
    while True:
        query = "limit=50" + (f"&cursor={urllib.parse.quote(cursor)}" if cursor else "")
        request = urllib.request.Request(
            f"{BASE_URL}/api/conversations?{query}",
            headers={"X-API-Key": API_KEY},
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            page = json.loads(response.read().decode("utf-8"))
            # Demo API returns a bare conversation list (the frontend helper
            # wraps it as {data: ...}).
            total += len(page)
            cursor = response.headers.get("X-Next-Cursor")
            if not cursor or response.headers.get("X-Has-More") != "true":
                return total


def queue_last_conversation_id() -> str:
    """Id of the queue's final (oldest-open) row, per the API sort order.

    Queried via API rather than hard-coded: the shared scratch DB accumulates
    rows from earlier UI suites, so the deep-most row is not necessarily a
    seeded ``vq-slot-*`` conversation on reruns.
    """
    cursor: str | None = None
    last_page: list | None = None
    while True:
        query = "limit=50" + (f"&cursor={urllib.parse.quote(cursor)}" if cursor else "")
        request = urllib.request.Request(
            f"{BASE_URL}/api/conversations?{query}",
            headers={"X-API-Key": API_KEY},
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            page = json.loads(response.read().decode("utf-8"))
            last_page = page
            cursor = response.headers.get("X-Next-Cursor")
            if not cursor or response.headers.get("X-Has-More") != "true":
                break
    assert last_page, "queue returned no conversations"
    return str(last_page[-1]["id"])


def seed_conversations() -> int:
    """Top the queue up to SEED_COUNT total rows; return the target count.

    Idempotent: keeps the scratch DB reusable across reruns instead of
    stacking extra rows each time.
    """
    existing = count_conversations()
    if existing >= SEED_COUNT:
        return existing
    needed = SEED_COUNT - existing
    for index in range(1, needed + 1):
        status = api_post(
            "/api/conversations",
            {"customer_name": f"vq-slot-{index:04d}", "channel": "web"},
        )
        if status != 201:
            raise AssertionError(f"Seed conversation {index} failed with HTTP {status}")
    return SEED_COUNT


def paginate_until_full(page: Page, target: int) -> None:
    deadline = time.monotonic() + 90
    while True:
        count_text = (page.locator("#queueCount").text_content() or "").strip()
        # "230 个会话" (all loaded, no more pages) or "230+ 个会话" (still more).
        if count_text.startswith(f"{target}"):
            return
        if time.monotonic() >= deadline:
            raise AssertionError(f"Queue never reached {target}+ rows (count={count_text!r})")
        button = page.locator("#loadMore")
        if button.is_visible() and button.is_enabled():
            button.click()
        page.wait_for_timeout(200)


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

    target = seed_conversations()
    assert target > VIRTUAL_THRESHOLD, (
        f"need more than {VIRTUAL_THRESHOLD} rows for virtual mode (have {target})"
    )

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
        expect(page.locator("#conversationList .conversation-item").first).to_be_visible()

        paginate_until_full(page, target)
        count_text = (page.locator("#queueCount").text_content() or "").strip()
        assert count_text.startswith(f"{target}"), count_text

        list_element = page.locator("#conversationList")
        rows = page.locator("#conversationList .conversation-row")
        rendered_count = rows.count()
        assert 0 < rendered_count < 60, (
            f"virtual mode should render a window, got {rendered_count} rows"
        )
        assert rows.count() > 0

        pads = page.locator("#conversationList .vqueue-pad")
        assert pads.count() >= 1, "expected at least the top spacer pad"

        pad_heights = list_element.evaluate(
            "el => Array.from(el.querySelectorAll('.vqueue-pad')).map(p => p.style.height)"
        )
        assert all(p and p.endswith("px") and "NaN" not in p for p in pad_heights), pad_heights

        total_height = list_element.evaluate("el => el.scrollHeight")
        assert total_height > 15000, f"scrollHeight {total_height} too small for 230 rows"

        # Bulk selection works inside the window: the checkbox, its row, and
        # the toolbar all stay consistent while only the visible band is in
        # the DOM. A windowed re-render can swap the node mid-click, so the
        # uncheck retries against the freshly re-resolved `:checked` input.
        page.locator("#conversationList .conversation-checkbox").first.check()
        expect(page.locator("#bulkToolbar")).to_be_visible()
        bulk_cleared = False
        for _ in range(12):
            try:
                page.locator("#conversationList .conversation-checkbox:checked").first.uncheck()
            except PlaywrightError:
                page.wait_for_timeout(120)
                continue
            if page.locator("#bulkToolbar").is_hidden():
                bulk_cleared = True
                break
            page.wait_for_timeout(120)
        assert bulk_cleared, "bulk toolbar stayed visible after unchecking the selected row"

        # Deep-scroll to the bottom of the list; the window must follow.
        first_band_ids = page.locator("#conversationList .conversation-row").evaluate_all(
            "nodes => nodes.map(n => n.querySelector('.conversation-item')?.dataset?.id)"
        )
        list_element.evaluate("el => { el.scrollTop = el.scrollHeight; }")
        page.wait_for_timeout(500)
        bottom_band_ids = page.locator("#conversationList .conversation-row").evaluate_all(
            "nodes => nodes.map(n => n.querySelector('.conversation-item')?.dataset?.id)"
        )
        assert len(set(bottom_band_ids)) == len(bottom_band_ids), "duplicate ids in window"
        assert set(bottom_band_ids) != set(first_band_ids), (
            "scrolling to the bottom did not re-render the window"
        )
        # 断言滚动到底后窗口包含队列真实末行(经 API 取,兼容共享 DB 上更老
        # 的测试会话占据底部)——而不是写死 vq-slot-0001。
        last_id = queue_last_conversation_id()
        assert last_id in bottom_band_ids, (
            f"deep-most row {last_id} not rendered after scrolling to bottom "
            f"(window has {len(bottom_band_ids)} ids)"
        )

        # Selection on a windowed, visible row still loads the detail view.
        # A windowed re-render can swap the first row between reading the
        # name and clicking it (the same race the bulk-uncheck loop handles
        # above), so retry until the loaded title matches the clicked row.
        selected = False
        for _ in range(12):
            first_visible = page.locator("#conversationList .conversation-item").first
            chosen_name = page.locator("#conversationList .item-name").first.text_content() or ""
            first_visible.click()
            try:
                expect(page.locator("#conversationTitle")).to_have_text(chosen_name, timeout=2000)
                selected = True
                break
            except AssertionError:
                page.wait_for_timeout(150)
        assert selected, "clicking a windowed row never loaded its detail"
        expect(page.locator("#messages")).to_be_visible()

        page.screenshot(path=ARTIFACTS / "ui-vqueue.png", full_page=True)
        browser.close()

    assert not console_errors, (
        f"Browser console errors: {console_errors}; HTTP errors: {http_errors}; "
        f"failed requests: {failed_requests}"
    )
    assert not page_errors, f"Unhandled page errors: {page_errors}"
    assert not http_errors, f"HTTP errors: {http_errors}"
    assert not failed_requests, f"Failed requests: {failed_requests}"
    print(json.dumps({"status": "ok", "screenshot": str(ARTIFACTS / "ui-vqueue.png")}))


if __name__ == "__main__":
    main()
