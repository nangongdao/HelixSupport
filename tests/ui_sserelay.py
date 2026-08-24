"""ROADMAP §18.4 — SSE single-connection relay across tabs (browser smoke).

Verifies that two tabs of the operator console in one browser share ONE
connection to `/api/events/queue` via BroadcastChannel leader election: after
the election settles, exactly one tab holds the stream while the other opens
none of its own, and a queue revision push (new conversation) reaches BOTH —
the follower through the leader's relayed event, not its own SSE.

The 30s background poll timer is the discriminator: both tabs must show the
new queue count well inside 30s, so only the relay path (or the leader's own
stream) can have driven the refresh.

Server is expected to already run on HELIX_BASE_URL backed by a throwaway
SQLite database (see tests/ui_virtual_queue.py for the same harness).

Run:
    python tests/ui_sserelay.py
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
ARTIFACTS = Path(__file__).resolve().parents[1] / "artifacts"
POLL_INTERVAL_NORMAL = 30  # app.js POLL_INTERVAL_NORMAL, in seconds
ASSERT_DEADLINE_S = POLL_INTERVAL_NORMAL - 15  # must beat the poll timer


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


def api_patch(path: str, body: dict) -> int:
    """PATCH helper — used to promote a conversation to high priority."""
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-API-Key": API_KEY},
        method="PATCH",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def instrument(page: Page) -> dict:
    """Per-tab counters: SSE stream fetches, console/page/HTTP failures."""
    tracker = {
        "sse_requests": 0,
        "console_errors": [],
        "page_errors": [],
        "http_errors": [],
        "failed_requests": [],
    }

    def on_request(request) -> None:
        if request.url.startswith(f"{BASE_URL}/api/events/queue"):
            tracker["sse_requests"] += 1

    def on_console(message) -> None:
        if message.type == "error":
            tracker["console_errors"].append(f"{message.text} @ {message.location}")

    def on_failed(request) -> None:
        failure = request.failure or ""
        if request.url.startswith(f"{BASE_URL}/api/events/queue") and "ERR_ABORTED" in failure:
            return
        tracker["failed_requests"].append(f"{request.method} {request.url} {failure}")

    page.on("request", on_request)
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


def _find_conversation_id(name: str) -> str | None:
    """Resolve the conversation id for ``name`` by walking the first list page.

    The queue list sorts high-priority rows first, so a freshly created
    (normal priority) conversation is most likely on page one.  The lookup
    walks a few pages to be safe without paging the entire history.
    """
    cursor: str | None = None
    for _ in range(5):
        query = "limit=50" + (f"&cursor={urllib.parse.quote(cursor)}" if cursor else "")
        request = urllib.request.Request(
            f"{BASE_URL}/api/conversations?{query}",
            headers={"X-API-Key": API_KEY},
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            page = json.loads(response.read().decode("utf-8"))
            for row in page:
                if row.get("customer_name") == name:
                    return str(row["id"])
            cursor = response.headers.get("X-Next-Cursor")
            if not cursor or response.headers.get("X-Has-More") != "true":
                break
    return None


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
            total += len(page)
            cursor = response.headers.get("X-Next-Cursor")
            if not cursor or response.headers.get("X-Has-More") != "true":
                return total


def wait_queue_settled(page: Page, timeout_s: float) -> None:
    """Wait until the queue pane has rendered (empty or not) on ``page``."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if (page.locator("#queueCount").text_content() or "").strip():
            return
        page.wait_for_timeout(200)
    raise AssertionError("#queueCount never rendered")


def wait_queue_has(page: Page, name: str, timeout_s: float) -> None:
    """Wait until the queue list renders a row named ``name``.

    Name-based instead of count-based because ``#queueCount`` caps at
    ``{page_size}+ 个会话`` once the database holds more than one page of
    conversations (shared scratch DB after ui_virtual_queue seeds hundreds
    of rows), so an exact count can never be observed there.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        names = page.locator("#conversationList .item-name").all_text_contents()
        if any(name in text for text in names):
            return
        page.wait_for_timeout(200)
    raise AssertionError(f"queue list never showed {name!r} (names={names!r})")


def main() -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except PlaywrightError:
            browser = playwright.chromium.launch(channel="msedge", headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        tab_a = context.new_page()
        tab_b = context.new_page()
        a = instrument(tab_a)
        b = instrument(tab_b)

        tab_a.goto(BASE_URL)
        tab_b.goto(BASE_URL)
        # Idempotent across reruns: the scratch DB may already hold rows (a
        # ui_virtual_queue run leaves hundreds behind), so the tabs only
        # anchor on the queue pane being rendered, then each seeded
        # conversation is asserted by name in both lists.
        for page in (tab_a, tab_b):
            wait_queue_settled(page, 8)

        # Election settles within probeWait(120ms)+SSE connect; wait for exactly
        # one tab to hold the stream and the other to never have asked for one.
        leader, follower = None, None
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            counts = {"A": a["sse_requests"], "B": b["sse_requests"]}
            if sorted(counts.values()) == [0, 1]:
                leader = tab_a if a["sse_requests"] == 1 else tab_b
                follower = tab_b if leader is tab_a else tab_a
                break
            tab_a.wait_for_timeout(200)
        assert leader is not None, f"election never settled to one stream: {counts!r}"
        assert follower is not None, "election settled leader but not follower"
        leader_label = "A" if leader is tab_a else "B"
        follower_label = "A" if follower is tab_a else "B"
        print(
            f"election settled: leader=tab {leader_label} (sse=1), "
            f"follower=tab {follower_label} (sse=0)"
        )

        # Seed two conversations one at a time; BOTH tabs must show each name
        # well before the 30s poll interval, proving the follower's update
        # came through the relayed event rather than its own stream or poll
        # timer.
        #
        # Each conversation is created then promoted to ``high`` priority via
        # ``PATCH /api/conversations/{id}`` so it sorts above the hundreds of
        # ``waiting_human`` rows the shared scratch DB may already hold
        # (load-test seeding promotes rows to high).  Without this the seeded
        # names fall past the first queue page and the per-name assertion can
        # never observe them inside the 30s relay deadline.
        seed_tag = str(int(time.time()))
        for index in (1, 2):
            seed_name = f"relay-{seed_tag}-{index:02d}"
            create_status = api_post(
                "/api/conversations",
                {"customer_name": seed_name, "channel": "web"},
            )
            assert create_status == 201, (
                f"seed conversation {seed_name} failed HTTP {create_status}"
            )
            # Fetch the freshly created conversation id so we can promote it.
            conv_id = _find_conversation_id(seed_name)
            assert conv_id, f"could not resolve id for {seed_name}"
            promote_status = api_patch(
                f"/api/conversations/{conv_id}",
                {"priority": "high"},
            )
            assert promote_status == 200, (
                f"promote {seed_name} to high failed HTTP {promote_status}"
            )
            wait_queue_has(leader, seed_name, ASSERT_DEADLINE_S)
            wait_queue_has(follower, seed_name, ASSERT_DEADLINE_S)

        # The follower must never have opened /api/events/queue at all.
        follower_tracker = a if follower is tab_a else b
        assert follower_tracker["sse_requests"] == 0, (
            f"follower opened {follower_tracker['sse_requests']} SSE streams"
        )

        leader.screenshot(path=ARTIFACTS / "ui-sserelay-leader.png")
        follower.screenshot(path=ARTIFACTS / "ui-sserelay-follower.png")
        browser.close()

    leader_tracker = a if leader is tab_a else b
    for label, tracker in (("leader", leader_tracker), ("follower", follower_tracker)):
        assert not tracker["console_errors"], f"{label} console errors: {tracker['console_errors']}"
        assert not tracker["page_errors"], f"{label} page errors: {tracker['page_errors']}"
        assert not tracker["http_errors"], f"{label} HTTP errors: {tracker['http_errors']}"
        assert not tracker["failed_requests"], (
            f"{label} failed requests: {tracker['failed_requests']}"
        )
    print(
        json.dumps(
            {
                "status": "ok",
                "leader_tab": leader_label,
                "follower_tab": follower_label,
                "sse_streams": 1,
                "screenshots": [
                    str(ARTIFACTS / "ui-sserelay-leader.png"),
                    str(ARTIFACTS / "ui-sserelay-follower.png"),
                ],
            }
        )
    )


if __name__ == "__main__":
    main()
