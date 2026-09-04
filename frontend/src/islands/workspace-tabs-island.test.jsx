/**
 * Helix Support — workspace tabs island tests (D3 long tail)
 *
 * The island owns the 队列/工单 tablist; pane switching and data loading
 * stay legacy via helix-workspace-tab/-changed. These tests cover the
 * optimistic switch, the reconciliation event, and the legacy class
 * contract — no legacy app.js.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import React from "react";

import {
  WorkspaceTabsIsland,
  WORKSPACE_TAB_EVENTS,
  reduceWorkspaceTabs,
} from "./workspace-tabs-island.jsx";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("reduceWorkspaceTabs", () => {
  it("returns the same reference when the tab is unchanged", () => {
    expect(reduceWorkspaceTabs("queue", "queue")).toBe("queue");
    expect(reduceWorkspaceTabs("queue", "tickets")).toBe("tickets");
  });
});

describe("WorkspaceTabsIsland", () => {
  it("renders the queue tab active by default with the legacy contract", () => {
    const { container } = render(<WorkspaceTabsIsland />);
    const tablist = container.querySelector(".workspace-tabs");
    expect(tablist.getAttribute("role")).toBe("tablist");
    const queue = screen.getByRole("tab", { name: "队列" });
    const tickets = screen.getByRole("tab", { name: "工单" });
    expect(queue.getAttribute("aria-selected")).toBe("true");
    expect(queue.classList.contains("is-active")).toBe(true);
    expect(queue.dataset.wstab).toBe("queue");
    expect(tickets.getAttribute("aria-selected")).toBe("false");
    expect(tickets.classList.contains("is-active")).toBe(false);
  });

  it("applies clicks optimistically and bridges helix-workspace-tab", () => {
    const dispatchSpy = vi.spyOn(window, "dispatchEvent");
    render(<WorkspaceTabsIsland />);
    fireEvent.click(screen.getByRole("tab", { name: "工单" }));
    const tickets = screen.getByRole("tab", { name: "工单" });
    expect(tickets.classList.contains("is-active")).toBe(true);
    expect(tickets.getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("tab", { name: "队列" }).getAttribute("aria-selected")).toBe("false");
    const switchEvent = dispatchSpy.mock.calls.map(([ev]) => ev).find(
      (ev) => ev.type === WORKSPACE_TAB_EVENTS.SWITCH,
    );
    expect(switchEvent.detail).toEqual({ field: "tickets" });
  });

  it("reconciles when legacy reports a programmatic tab change", () => {
    render(<WorkspaceTabsIsland />);
    // Legacy resets to "queue" on ticket-jump — a programmatic switch the
    // island must follow even though it never clicked.
    act(() => {
      window.dispatchEvent(
        new CustomEvent(WORKSPACE_TAB_EVENTS.CHANGED, { detail: { field: "queue" } }),
      );
    });
    expect(screen.getByRole("tab", { name: "队列" }).classList.contains("is-active")).toBe(true);
    act(() => {
      window.dispatchEvent(
        new CustomEvent(WORKSPACE_TAB_EVENTS.CHANGED, { detail: { field: "tickets" } }),
      );
    });
    expect(screen.getByRole("tab", { name: "工单" }).classList.contains("is-active")).toBe(true);
  });

  it("ignores changed events with unknown fields", () => {
    render(<WorkspaceTabsIsland />);
    act(() => {
      window.dispatchEvent(
        new CustomEvent(WORKSPACE_TAB_EVENTS.CHANGED, { detail: { field: "bogus" } }),
      );
    });
    expect(screen.getByRole("tab", { name: "队列" }).classList.contains("is-active")).toBe(true);
  });
});
