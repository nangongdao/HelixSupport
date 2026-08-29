/**
 * Helix Support — queue island component tests (D3)
 *
 * The island is a pure mirror of the legacy queue: it listens for
 * helix-conversations-updated snapshots and renders the same row classes as
 * queueRowHtml. These tests exercise the mirror contract against @testing-
 * library/react — no legacy app.js, no network.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import React from "react";

import { QueueIsland, QUEUE_EVENTS } from "../islands/queue-island.jsx";

function makeConversation(overrides = {}) {
  return {
    id: "conv_1",
    customer_name: "林嘉",
    status: "open",
    assigned_agent: null,
    intent: "order_status",
    preview: "请帮我查询订单",
    labels: ["VIP"],
    sla_due_at: "2026-08-28T10:00:00Z",
    claim_active: false,
    ...overrides,
  };
}

function publish(detail) {
  window.dispatchEvent(new CustomEvent(QUEUE_EVENTS.UPDATED, { detail }));
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("QueueIsland mirror contract", () => {
  it("renders rows with the legacy row classes once a snapshot arrives", async () => {
    render(<QueueIsland />);
    publish({
      conversations: [makeConversation()],
      selectedId: null,
      bulkSelected: [],
      canOperate: true,
      compact: false,
    });
    const row = await waitFor(() => screen.getByRole("button", { name: /林嘉/ }));
    expect(row.closest(".conversation-row")).toBeTruthy();
    expect(row).toHaveProperty("className", expect.stringContaining("conversation-item"));
    expect(row.getAttribute("data-id")).toBe("conv_1");
    // Backend enum label for open status (mirrors legacy statusLabel).
    expect(screen.getByText("自动处理中")).toBeTruthy();
  });

  it("renders the SLA breach state for an overdue conversation", async () => {
    render(<QueueIsland />);
    publish({
      conversations: [
        makeConversation({ sla_due_at: "2020-01-01T00:00:00Z", sla_breached: true }),
      ],
      selectedId: null,
      bulkSelected: [],
      canOperate: true,
      compact: false,
    });
    const sla = await waitFor(() => screen.getByText(/超时/));
    expect(sla.closest(".item-sla").classList.contains("is-breached")).toBe(true);
  });

  it("dispatches helix-queue-select when a row is clicked", async () => {
    const dispatchSpy = vi.spyOn(window, "dispatchEvent");
    render(<QueueIsland />);
    publish({
      conversations: [makeConversation()],
      selectedId: null,
      bulkSelected: [],
      canOperate: true,
      compact: false,
    });
    fireEvent.click(await waitFor(() => screen.getByRole("button", { name: /林嘉/ })));
    const selectEvent = dispatchSpy.mock.calls.map(([ev]) => ev).find(
      (ev) => ev.type === QUEUE_EVENTS.SELECT,
    );
    expect(selectEvent).toBeTruthy();
    expect(selectEvent.detail).toEqual({ id: "conv_1" });
  });

  it("dispatches helix-queue-bulk when a row checkbox toggles", async () => {
    const dispatchSpy = vi.spyOn(window, "dispatchEvent");
    render(<QueueIsland />);
    publish({
      conversations: [makeConversation()],
      selectedId: null,
      bulkSelected: [],
      canOperate: true,
      compact: false,
    });
    const checkbox = await waitFor(() => screen.getByLabelText("选择 林嘉"));
    fireEvent.click(checkbox);
    const bulkEvent = dispatchSpy.mock.calls.map(([ev]) => ev).find(
      (ev) => ev.type === QUEUE_EVENTS.BULK,
    );
    expect(bulkEvent).toBeTruthy();
    expect(bulkEvent.detail).toEqual({ id: "conv_1", on: true });
    expect(checkbox.checked).toBe(true);
  });

  it("shows the empty state when a snapshot has no conversations", async () => {
    render(<QueueIsland />);
    publish({ conversations: [], selectedId: null, bulkSelected: [], canOperate: true, compact: false });
    expect(await waitFor(() => screen.getByText("当前筛选条件下没有会话"))).toBeTruthy();
  });

  it("renders the syncing placeholder plus a zeroed strip before the first snapshot", () => {
    // Island owns the strip now, so even the pre-snapshot frame mirrors
    // legacy's initial "0 个会话" instead of rendering nothing at all.
    const { container } = render(<QueueIsland />);
    expect(screen.getByRole("status", { name: "队列加载中" })).toBeTruthy();
    expect(screen.getByText("0 个会话")).toBeTruthy();
    expect(container.querySelector(".queue-island")).toBeTruthy();
  });
});

describe("QueueIsland footer strip", () => {
  it("renders the count with a + suffix while more pages exist", async () => {
    render(<QueueIsland />);
    publish({
      conversations: [makeConversation()],
      selectedId: null,
      bulkSelected: [],
      canOperate: true,
      compact: false,
      queueHasMore: true,
    });
    expect(await waitFor(() => screen.getByText("1+ 个会话"))).toBeTruthy();
    const more = screen.getByRole("button", { name: /加载更多/ });
    expect(more.hidden).toBe(false);
    expect(more.disabled).toBe(false);
  });

  it("drops the + and hides 加载更多 when no more pages remain", async () => {
    const { container } = render(<QueueIsland />);
    publish({
      conversations: [makeConversation()],
      selectedId: null,
      bulkSelected: [],
      canOperate: true,
      compact: false,
      queueHasMore: false,
    });
    expect(await waitFor(() => screen.getByText("1 个会话"))).toBeTruthy();
    // hidden 按钮不进可访问性树,按 DOM 断言其隐藏态。
    const more = container.querySelector(".queue-more");
    expect(more.hidden).toBe(true);
  });

  it("marks the load-more button busy while a page fetch is in flight", async () => {
    render(<QueueIsland />);
    publish({
      conversations: [makeConversation()],
      selectedId: null,
      bulkSelected: [],
      canOperate: true,
      compact: false,
      queueHasMore: true,
      queueLoadingMore: true,
    });
    const more = await waitFor(() => screen.getByRole("button", { name: /加载更多/ }));
    expect(more.disabled).toBe(true);
    expect(more.getAttribute("aria-busy")).toBe("true");
  });

  it("dispatches helix-queue-load-more when the button is clicked", async () => {
    const dispatchSpy = vi.spyOn(window, "dispatchEvent");
    render(<QueueIsland />);
    publish({
      conversations: [makeConversation()],
      selectedId: null,
      bulkSelected: [],
      canOperate: true,
      compact: false,
      queueHasMore: true,
    });
    fireEvent.click(await waitFor(() => screen.getByRole("button", { name: /加载更多/ })));
    const loadMoreEvent = dispatchSpy.mock.calls.map(([ev]) => ev).find(
      (ev) => ev.type === QUEUE_EVENTS.LOAD_MORE,
    );
    expect(loadMoreEvent).toBeTruthy();
  });
});