/**
 * Helix Support — queue island component tests (D3)
 *
 * The island is a pure mirror of the legacy queue: it listens for
 * helix-conversations-updated snapshots and renders the same row classes as
 * queueRowHtml. These tests exercise the mirror contract against @testing-
 * library/react — no legacy app.js, no network.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
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

describe("QueueIsland bulk toolbar", () => {
  const base = {
    conversations: [makeConversation()],
    selectedId: null,
    bulkSelected: [],
    canOperate: true,
    compact: false,
  };

  it("shows the toolbar with the selection count once rows are selected", async () => {
    render(<QueueIsland />);
    publish({ ...base, bulkSelected: ["conv_1"] });
    await waitFor(() => expect(screen.getByText("已选 1 项")).toBeTruthy());
    expect(document.querySelector(".bulk-toolbar .bulk-action-field")).toBeTruthy();
  });

  it("shows the label field only for add/remove-label actions", async () => {
    render(<QueueIsland />);
    publish({ ...base, bulkSelected: ["conv_1"] });
    await waitFor(() => expect(screen.getByText("已选 1 项")).toBeTruthy());
    expect(screen.queryByLabelText("批量标签")).toBeNull();
    fireEvent.change(screen.getByLabelText("批量操作"), {
      target: { value: "add-label" },
    });
    expect(screen.getByLabelText("批量标签")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("批量操作"), {
      target: { value: "claim" },
    });
    expect(screen.queryByLabelText("批量标签")).toBeNull();
  });

  it("bridges apply with the parsed labels for label actions", async () => {
    const dispatchSpy = vi.spyOn(window, "dispatchEvent");
    render(<QueueIsland />);
    publish({ ...base, bulkSelected: ["conv_1", "conv_2"] });
    await waitFor(() => expect(screen.getByText("已选 2 项")).toBeTruthy());
    fireEvent.change(screen.getByLabelText("批量操作"), {
      target: { value: "add-label" },
    });
    fireEvent.change(screen.getByLabelText("批量标签"), {
      target: { value: " VIP， 退款风险 " },
    });
    fireEvent.click(screen.getByRole("button", { name: "应用批量操作" }));
    const applyEvent = dispatchSpy.mock.calls.map(([ev]) => ev).find(
      (ev) => ev.type === QUEUE_EVENTS.BULK_APPLY,
    );
    expect(applyEvent.detail).toEqual({
      action: "add-label",
      labels: ["VIP", "退款风险"],
    });
  });

  it("blocks label actions with empty labels via an inline alert", async () => {
    const dispatchSpy = vi.spyOn(window, "dispatchEvent");
    render(<QueueIsland />);
    publish({ ...base, bulkSelected: ["conv_1"] });
    await waitFor(() => expect(screen.getByText("已选 1 项")).toBeTruthy());
    fireEvent.change(screen.getByLabelText("批量操作"), {
      target: { value: "remove-label" },
    });
    fireEvent.click(screen.getByRole("button", { name: "应用批量操作" }));
    // legacy applyBulkAction 的「请输入标签」文案,岛内联呈现且不发桥。
    expect(screen.getByRole("alert").textContent).toBe("请输入标签");
    expect(
      dispatchSpy.mock.calls.some(([ev]) => ev.type === QUEUE_EVENTS.BULK_APPLY),
    ).toBe(false);
  });

  it("releases the busy apply button when the bridge reports completion", async () => {
    render(<QueueIsland />);
    publish({ ...base, bulkSelected: ["conv_1"] });
    await waitFor(() => expect(screen.getByText("已选 1 项")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "应用批量操作" }));
    const applyButton = screen.getByRole("button", { name: "应用批量操作" });
    expect(applyButton.disabled).toBe(true);
    act(() => {
      window.dispatchEvent(new CustomEvent(QUEUE_EVENTS.BULK_APPLIED));
    });
    expect(applyButton.disabled).toBe(false);
  });

  it("hides the toolbar for operators without selection or without canOperate", async () => {
    render(<QueueIsland />);
    publish(base);
    expect(document.querySelector(".bulk-toolbar")).toBeNull();
    publish({ ...base, canOperate: false, bulkSelected: ["conv_1"] });
    expect(document.querySelector(".bulk-toolbar")).toBeNull();
  });

  it("dispatches helix-queue-bulk-clear on the clear button", async () => {
    const dispatchSpy = vi.spyOn(window, "dispatchEvent");
    render(<QueueIsland />);
    publish({ ...base, bulkSelected: ["conv_1"] });
    await waitFor(() => expect(screen.getByText("已选 1 项")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "清除选择" }));
    const clearEvent = dispatchSpy.mock.calls.map(([ev]) => ev).find(
      (ev) => ev.type === QUEUE_EVENTS.BULK_CLEAR,
    );
    expect(clearEvent).toBeTruthy();
  });
});
