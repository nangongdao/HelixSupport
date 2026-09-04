/**
 * Helix Support — quality island tests (D3 long tail glue slice)
 *
 * The quality island has rendered the buckets since D2; this slice added
 * the refresh bridge (helix-quality-refresh) that mirrors the legacy 10s
 * throttle. These tests cover the refresh semantics against a stubbed
 * fetch — chart internals are unchanged D2 behaviour.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";

import { QualityIsland, QUALITY_EVENTS } from "./quality-island.jsx";

const BUCKETS = [
  { date: "2026-08-28", intent: "退款", prompt_version: 3, turn_count: 4, escalation_count: 1, resolution_count: 3, avg_confidence: 0.8 },
  { date: "2026-08-29", intent: "退款", prompt_version: 3, turn_count: 6, escalation_count: 0, resolution_count: 6, avg_confidence: 0.9 },
];

function stubBackend() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({ ok: true, status: 200, json: async () => BUCKETS })),
  );
}

async function renderIsland() {
  stubBackend();
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <QualityIsland />
    </QueryClientProvider>,
  );
  // Flush the mount query (fetch → state update) inside act so the data
  // lands before any assertion.
  await act(async () => {
    await waitFor(() => expect(fetch).toHaveBeenCalled());
  });
  return client;
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("QualityIsland refresh bridge", () => {
  it("renders the buckets after the first fetch", async () => {
    await renderIsland();
    await waitFor(() => expect(document.querySelector(".qc-trend")).toBeTruthy());
    expect(document.querySelector(".qc-island")).toBeTruthy();
  });

  it("refetches on a forced helix-quality-refresh", async () => {
    await renderIsland();
    const before = fetch.mock.calls.length;
    act(() => {
      window.dispatchEvent(
        new CustomEvent(QUALITY_EVENTS.REFRESH, { detail: { force: true } }),
      );
    });
    await waitFor(() => expect(fetch.mock.calls.length).toBeGreaterThan(before));
  });

  it("skips the refetch when an unforced refresh finds fresh data (10s throttle)", async () => {
    await renderIsland();
    const before = fetch.mock.calls.length;
    act(() => {
      window.dispatchEvent(new CustomEvent(QUALITY_EVENTS.REFRESH));
    });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(fetch.mock.calls.length).toBe(before);
  });
});
