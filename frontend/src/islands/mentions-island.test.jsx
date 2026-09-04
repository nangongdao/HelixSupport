/**
 * Helix Support — mentions island tests (D3 long tail)
 *
 * The island owns the mentions badge (portal) and panel drawer; mark-read
 * and conversation jumps stay legacy via helix-mentions bridges. These
 * tests cover the permission gate, badge visibility semantics, panel
 * rendering, and the bridge contract — no legacy app.js, only a stubbed
 * fetch.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, act, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";

import {
  MentionsIsland,
  MENTION_EVENTS,
  MENTION_IDS,
} from "./mentions-island.jsx";

function makeMention(overrides = {}) {
  return {
    id: "men_1",
    conversation_id: "conv_1",
    conversation_customer: "林嘉",
    channel: "web",
    created_at: "2026-08-29T08:00:00Z",
    unread: true,
    note_preview: "@你 看一下这个订单",
    mentioned_by: "supervisor.1",
    ...overrides,
  };
}

function stubBackend(payload) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({ ok: true, status: 200, json: async () => payload })),
  );
}

function grantReader() {
  window.__HELIX_PERMISSIONS__ = ["conversation:read"];
  window.__HELIX_ACTOR__ = "demo.admin";
  act(() => {
    window.dispatchEvent(
      new CustomEvent(MENTION_EVENTS.IDENTITY, {
        detail: { role: "admin", permissions: ["conversation:read"], actorId: "demo.admin", tenantId: "demo" },
      }),
    );
  });
}

async function renderIsland(payload = { mentions: [makeMention()], unread_count: 1 }) {
  stubBackend(payload);
  // Portal target for the badge (the island renders into it by id lookup).
  const badgeHost = document.createElement("div");
  badgeHost.id = "mentionsBadgeReactIsland";
  document.body.appendChild(badgeHost);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  grantReader();
  render(
    <QueryClientProvider client={client}>
      <MentionsIsland />
    </QueryClientProvider>,
  );
  await waitFor(() => expect(document.getElementById(MENTION_IDS.badge)).toBeTruthy());
  return { badgeHost };
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  document.getElementById("mentionsBadgeReactIsland")?.remove();
});

describe("MentionsIsland", () => {
  it("hides the badge before identity grants conversation:read", async () => {
    const badgeHost = document.createElement("div");
    badgeHost.id = "mentionsBadgeReactIsland";
    document.body.appendChild(badgeHost);
    stubBackend({ mentions: [], unread_count: 0 });
    render(
      <QueryClientProvider client={new QueryClient()}>
        <MentionsIsland />
      </QueryClientProvider>,
    );
    const badge = await waitFor(() => {
      const el = document.getElementById(MENTION_IDS.badge);
      expect(el).toBeTruthy();
      return el;
    });
    expect(badge.hidden).toBe(true);
    expect(fetch).not.toHaveBeenCalled();
    badgeHost.remove();
  });

  it("shows the badge with the unread count and keeps legacy classes", async () => {
    await renderIsland();
    const badge = document.getElementById(MENTION_IDS.badge);
    await waitFor(() => expect(badge.hidden).toBe(false));
    expect(badge.className).toBe("mentions-badge");
    expect(document.getElementById(MENTION_IDS.count).textContent).toBe("1");
    expect(document.getElementById(MENTION_IDS.count).classList.contains("is-active")).toBe(true);
  });

  it("hides the badge when unread is 0 and the panel is closed", async () => {
    await renderIsland({ mentions: [], unread_count: 0 });
    expect(document.getElementById(MENTION_IDS.badge).hidden).toBe(true);
  });

  it("opens the panel on badge click and renders the mention rows", async () => {
    await renderIsland();
    fireEvent.click(document.getElementById(MENTION_IDS.badge));
    expect(await waitFor(() => screen.getByText("我的被提及"))).toBeTruthy();
    expect(screen.getByText("林嘉")).toBeTruthy();
    expect(screen.getByText(/未读/)).toBeTruthy();
    // Opening with unread > 0 keeps the badge visible.
    expect(document.getElementById(MENTION_IDS.badge).hidden).toBe(false);
  });

  it("shows the legacy empty state when there are no mentions", async () => {
    await renderIsland({ mentions: [], unread_count: 0 });
    fireEvent.click(document.getElementById(MENTION_IDS.badge));
    expect(await waitFor(() => screen.getByText("暂无被提及"))).toBeTruthy();
  });

  it("bridges the conversation jump and closes the panel", async () => {
    const dispatchSpy = vi.spyOn(window, "dispatchEvent");
    await renderIsland();
    fireEvent.click(document.getElementById(MENTION_IDS.badge));
    const link = await waitFor(() => screen.getByRole("button", { name: /林嘉/ }));
    fireEvent.click(link);
    const jump = dispatchSpy.mock.calls.map(([ev]) => ev).find(
      (ev) => ev.type === MENTION_EVENTS.OPEN_JUMP,
    );
    expect(jump.detail).toEqual({ conversationId: "conv_1" });
    expect(screen.queryByText("我的被提及")).toBeNull();
  });

  it("bridges mark-read for unread rows and refetches on changed", async () => {
    const dispatchSpy = vi.spyOn(window, "dispatchEvent");
    await renderIsland();
    fireEvent.click(document.getElementById(MENTION_IDS.badge));
    const readButton = await waitFor(() => screen.getByRole("button", { name: "标记已读" }));
    fireEvent.click(readButton);
    const read = dispatchSpy.mock.calls.map(([ev]) => ev).find(
      (ev) => ev.type === MENTION_EVENTS.MARK_READ,
    );
    expect(read.detail).toEqual({ id: "men_1" });
    // Legacy reports completion -> the island refetches (badge count drops).
    stubBackend({ mentions: [makeMention({ unread: false })], unread_count: 0 });
    act(() => {
      window.dispatchEvent(new CustomEvent(MENTION_EVENTS.CHANGED));
    });
    await waitFor(() =>
      expect(document.getElementById(MENTION_IDS.count).textContent).toBe("0"),
    );
  });

  it("closes on an outside click like legacy bindSession", async () => {
    await renderIsland();
    fireEvent.click(document.getElementById(MENTION_IDS.badge));
    await waitFor(() => expect(screen.getByText("我的被提及")).toBeTruthy());
    fireEvent.click(document.body);
    expect(screen.queryByText("我的被提及")).toBeNull();
  });
});
