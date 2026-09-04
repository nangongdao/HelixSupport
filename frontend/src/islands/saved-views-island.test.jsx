/**
 * Helix Support — saved views island tests (D3 long tail)
 *
 * The island owns the select/save/delete controls; the data lifecycle
 * (apply filters, POST with currentViewFilters, DELETE) stays legacy via
 * helix-saved-views-apply/-save/-delete/-changed. These tests cover the
 * bridge contract and selection semantics — no legacy app.js, only a
 * stubbed fetch.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, act, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";

import {
  SavedViewsIsland,
  SAVED_VIEW_EVENTS,
  SAVED_VIEW_IDS,
} from "./saved-views-island.jsx";

function makeView(overrides = {}) {
  return {
    id: "view_1",
    name: "高优待响应",
    filters: { status: "waiting_human", priority: "high" },
    ...overrides,
  };
}

function stubBackend(views) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({ ok: true, status: 200, json: async () => views })),
  );
}

function renderIsland(views = [makeView()]) {
  stubBackend(views);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <SavedViewsIsland />
    </QueryClientProvider>,
  );
  return waitFor(() => expect(screen.getByText(viewName(views))).toBeTruthy());
}

function viewName(views) {
  return views[0]?.name || "";
}

function publishChanged(detail) {
  act(() => {
    window.dispatchEvent(new CustomEvent(SAVED_VIEW_EVENTS.CHANGED, { detail }));
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("SavedViewsIsland", () => {
  it("renders the placeholder and loads views into the select", async () => {
    await renderIsland([makeView(), makeView({ id: "view_2", name: "退款风险" })]);
    const select = document.getElementById(SAVED_VIEW_IDS.select);
    const names = [...select.options].map((o) => o.textContent);
    expect(names).toEqual(["保存的视图", "高优待响应", "退款风险"]);
    expect(select.value).toBe("");
  });

  it("keeps the legacy class contract on all three controls", async () => {
    await renderIsland();
    expect(document.querySelector("label.saved-view-field")).toBeTruthy();
    expect(document.getElementById(SAVED_VIEW_IDS.save).className).toContain("workspace-icon-button");
    expect(document.getElementById(SAVED_VIEW_IDS.remove).disabled).toBe(true);
  });

  it("bridges apply with the full view object on selection", async () => {
    const dispatchSpy = vi.spyOn(window, "dispatchEvent");
    await renderIsland();
    const view = makeView();
    fireEvent.change(document.getElementById(SAVED_VIEW_IDS.select), {
      target: { value: view.id },
    });
    const applyEvent = dispatchSpy.mock.calls.map(([ev]) => ev).find(
      (ev) => ev.type === SAVED_VIEW_EVENTS.APPLY,
    );
    expect(applyEvent.detail).toEqual({ view });
    expect(document.getElementById(SAVED_VIEW_IDS.remove).disabled).toBe(false);
  });

  it("prompts for a name and bridges save with the trimmed name", async () => {
    const dispatchSpy = vi.spyOn(window, "dispatchEvent");
    vi.spyOn(window, "prompt").mockReturnValue("  我的视图  ");
    await renderIsland();
    fireEvent.click(document.getElementById(SAVED_VIEW_IDS.save));
    const saveEvent = dispatchSpy.mock.calls.map(([ev]) => ev).find(
      (ev) => ev.type === SAVED_VIEW_EVENTS.SAVE,
    );
    expect(saveEvent.detail).toEqual({ name: "我的视图" });
  });

  it("skips the save bridge when the prompt is cancelled", async () => {
    const dispatchSpy = vi.spyOn(window, "dispatchEvent");
    vi.spyOn(window, "prompt").mockReturnValue(null);
    await renderIsland();
    fireEvent.click(document.getElementById(SAVED_VIEW_IDS.save));
    expect(
      dispatchSpy.mock.calls.some(([ev]) => ev.type === SAVED_VIEW_EVENTS.SAVE),
    ).toBe(false);
  });

  it("reselects the created view when the changed event carries its id", async () => {
    await renderIsland();
    stubBackend([makeView(), makeView({ id: "view_new", name: "新建的" })]);
    publishChanged({ ok: true, id: "view_new" });
    await waitFor(() =>
      expect(document.getElementById(SAVED_VIEW_IDS.select).value).toBe("view_new"),
    );
    const names = [...document.getElementById(SAVED_VIEW_IDS.select).options].map(
      (o) => o.textContent,
    );
    expect(names).toContain("新建的");
  });

  it("drops the selection when a delete is reported without an id", async () => {
    await renderIsland();
    fireEvent.change(document.getElementById(SAVED_VIEW_IDS.select), {
      target: { value: "view_1" },
    });
    stubBackend([]);
    publishChanged({ ok: true });
    await waitFor(() =>
      expect(document.getElementById(SAVED_VIEW_IDS.select).value).toBe(""),
    );
    expect(document.getElementById(SAVED_VIEW_IDS.remove).disabled).toBe(true);
  });

  it("bridges delete with the selected id", async () => {
    const dispatchSpy = vi.spyOn(window, "dispatchEvent");
    await renderIsland();
    fireEvent.change(document.getElementById(SAVED_VIEW_IDS.select), {
      target: { value: "view_1" },
    });
    fireEvent.click(document.getElementById(SAVED_VIEW_IDS.remove));
    const deleteEvent = dispatchSpy.mock.calls.map(([ev]) => ev).find(
      (ev) => ev.type === SAVED_VIEW_EVENTS.DELETE,
    );
    expect(deleteEvent.detail).toEqual({ id: "view_1" });
  });

  it("ignores failed changes without touching the selection", async () => {
    await renderIsland();
    fireEvent.change(document.getElementById(SAVED_VIEW_IDS.select), {
      target: { value: "view_1" },
    });
    publishChanged({ ok: false });
    expect(document.getElementById(SAVED_VIEW_IDS.select).value).toBe("view_1");
  });
});
