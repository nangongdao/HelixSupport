/**
 * Helix Support — dashboard island component tests (D3 long tail)
 *
 * The island owns the workspace metrics strip; legacy foreground
 * refreshAll cycles drive refetches via helix-dashboard-refresh. These
 * tests cover the metricsModel net-render parity with legacy
 * renderMetrics (the 待人工→待响应 patch) and the refresh contract — no
 * legacy app.js, only a stubbed fetch.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";

import { DashboardIsland, DASHBOARD_EVENTS, metricsModel } from "./dashboard-island.jsx";

function makeDashboard(overrides = {}) {
  return {
    open: 12,
    waiting_human: 3,
    needs_response: 5,
    claimed_active: 2,
    sla_breached: 1,
    ...overrides,
  };
}

function stubBackend(payload) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({ ok: true, status: 200, json: async () => payload })),
  );
}

async function renderIsland(payload = makeDashboard()) {
  stubBackend(payload);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <DashboardIsland />
    </QueryClientProvider>,
  );
  await waitFor(() => expect(screen.getByText("自动")).toBeTruthy());
  return { client };
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("metricsModel parity with legacy renderMetrics", () => {
  it("shows the net tile set — 待响应 replaces 待人工", () => {
    // Legacy builds 待人工 first, then overwrites children[1] with
    // 待响应/needs_response before paint; the visible set is these four.
    expect(metricsModel(makeDashboard())).toEqual([
      ["自动", 12, false],
      ["待响应", 5, true],
      ["认领中", 2, false],
      ["SLA 超时", 1, true],
    ]);
  });

  it("alerts only when needs_response / sla_breached exceed zero", () => {
    const quiet = metricsModel(makeDashboard({ needs_response: 0, sla_breached: 0 }));
    expect(quiet.map(([label, , alert]) => alert)).toEqual([false, false, false, false]);
  });

  it("renders zeros for missing data like legacy renderMetrics(undefined)", () => {
    expect(metricsModel(undefined)).toEqual([
      ["自动", 0, false],
      ["待响应", 0, false],
      ["认领中", 0, false],
      ["SLA 超时", 0, false],
    ]);
  });
});

describe("DashboardIsland", () => {
  it("renders the four tiles with the legacy class contract", async () => {
    await renderIsland();
    const grid = document.querySelector(".metric-grid");
    expect(grid.getAttribute("aria-live")).toBe("polite");
    const tiles = [...grid.querySelectorAll(".metric")];
    expect(tiles.map((tile) => tile.querySelector("span").textContent)).toEqual([
      "自动", "待响应", "认领中", "SLA 超时",
    ]);
    expect(tiles.map((tile) => tile.querySelector("strong").textContent)).toEqual([
      "12", "5", "2", "1",
    ]);
    const alerts = tiles.filter((tile) => tile.classList.contains("is-alert"));
    expect(alerts.map((tile) => tile.querySelector("span").textContent)).toEqual([
      "待响应", "SLA 超时",
    ]);
  });

  it("sends the legacy tenant header", async () => {
    document.documentElement.dataset.tenantId = "demo";
    await renderIsland();
    expect(fetch).toHaveBeenCalledWith(
      "/api/dashboard",
      expect.objectContaining({ headers: { "X-Tenant-Id": "demo" } }),
    );
    delete document.documentElement.dataset.tenantId;
  });

  it("paints an empty grid instead of zeros until the first fetch lands", () => {
    stubBackend(makeDashboard());
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <DashboardIsland />
      </QueryClientProvider>,
    );
    const grid = document.querySelector(".metric-grid");
    expect(grid).toBeTruthy();
    expect(grid.dataset.loading).toBe("true");
    expect(grid.querySelector(".metric")).toBeNull();
  });

  it("refetches on a forced helix-dashboard-refresh", async () => {
    await renderIsland();
    const before = fetch.mock.calls.length;
    act(() => {
      window.dispatchEvent(
        new CustomEvent(DASHBOARD_EVENTS.REFRESH, { detail: { force: true } }),
      );
    });
    await waitFor(() => expect(fetch.mock.calls.length).toBeGreaterThan(before));
  });

  it("ignores unforced refresh events (background cycles dispatch nothing)", async () => {
    await renderIsland();
    const before = fetch.mock.calls.length;
    act(() => {
      window.dispatchEvent(new CustomEvent(DASHBOARD_EVENTS.REFRESH));
    });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(fetch.mock.calls.length).toBe(before);
  });
});
