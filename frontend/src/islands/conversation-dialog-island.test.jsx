/**
 * Helix Support — conversation dialog island tests (D3 long tail)
 *
 * The island owns the new-conversation <dialog>; legacy app.js keeps the
 * create lifecycle via helix-conversation-create/-created. These tests
 * cover the payload projection parity, the open/close contract, and the
 * bridge — no legacy app.js.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, act, waitFor } from "@testing-library/react";
import React from "react";

import {
  ConversationDialogIsland,
  DIALOG_EVENTS,
  DIALOG_IDS,
  conversationPayload,
} from "./conversation-dialog-island.jsx";

function renderIsland() {
  return render(<ConversationDialogIsland />);
}

function openIslandDialog() {
  act(() => {
    window.dispatchEvent(new CustomEvent(DIALOG_EVENTS.NEW));
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("conversationPayload parity with legacy submit", () => {
  it("trims fields and omits an empty customer_ref", () => {
    expect(
      conversationPayload({ customerName: "  林嘉  ", customerRef: "  ", channel: "web" }),
    ).toEqual({ customer_name: "林嘉", channel: "web" });
    expect(
      conversationPayload({ customerName: "林嘉", customerRef: " CUST-9 ", channel: "api" }),
    ).toEqual({ customer_name: "林嘉", customer_ref: "CUST-9", channel: "api" });
  });

  it("returns null when customer_name is blank (legacy guard)", () => {
    expect(conversationPayload({ customerName: "   ", customerRef: "", channel: "web" })).toBeNull();
    expect(conversationPayload({})).toBeNull();
  });
});

describe("ConversationDialogIsland", () => {
  it("renders the dialog closed until helix-conversation-new arrives", () => {
    renderIsland();
    const dialog = document.querySelector("dialog.dialog");
    expect(dialog).toBeTruthy();
    expect(dialog.open).toBe(false);
    expect(document.getElementById(DIALOG_IDS.form)).toBeTruthy();
  });

  it("opens blank and focuses the name field on helix-conversation-new", async () => {
    renderIsland();
    openIslandDialog();
    const dialog = document.querySelector("dialog.dialog");
    // The deferred showModal/focus runs on a 0ms timeout — poll, don't sleep.
    await waitFor(() => expect(dialog.open).toBe(true));
    expect(document.getElementById(DIALOG_IDS.customerName).value).toBe("");
    expect(document.activeElement).toBe(document.getElementById(DIALOG_IDS.customerName));
  });

  it("bridges the trimmed payload and reports busy while waiting", async () => {
    const dispatchSpy = vi.spyOn(window, "dispatchEvent");
    renderIsland();
    openIslandDialog();
    await waitFor(() => expect(document.querySelector("dialog.dialog").open).toBe(true));
    fireEvent.change(document.getElementById(DIALOG_IDS.customerName), {
      target: { value: "  林嘉  " },
    });
    fireEvent.change(document.getElementById(DIALOG_IDS.customerRef), {
      target: { value: " CUST-9 " },
    });
    fireEvent.change(document.getElementById(DIALOG_IDS.channel), {
      target: { value: "messaging" },
    });
    fireEvent.submit(document.getElementById(DIALOG_IDS.form));
    const create = dispatchSpy.mock.calls
      .map(([ev]) => ev)
      .find((ev) => ev.type === DIALOG_EVENTS.CREATE);
    expect(create.detail).toEqual({
      payload: { customer_name: "林嘉", customer_ref: "CUST-9", channel: "messaging" },
    });
    expect(document.getElementById(DIALOG_IDS.form).getAttribute("aria-busy")).toBe("true");
  });

  it("closes when legacy reports a successful create", async () => {
    renderIsland();
    openIslandDialog();
    await waitFor(() => expect(document.querySelector("dialog.dialog").open).toBe(true));
    const dialog = document.querySelector("dialog.dialog");
    fireEvent.submit(document.getElementById(DIALOG_IDS.form));
    act(() => {
      window.dispatchEvent(new CustomEvent(DIALOG_EVENTS.CREATED, { detail: { ok: true } }));
    });
    expect(dialog.open).toBe(false);
  });

  it("stays open and clears busy when the create fails", async () => {
    renderIsland();
    openIslandDialog();
    await waitFor(() => expect(document.querySelector("dialog.dialog").open).toBe(true));
    const dialog = document.querySelector("dialog.dialog");
    fireEvent.submit(document.getElementById(DIALOG_IDS.form));
    act(() => {
      window.dispatchEvent(new CustomEvent(DIALOG_EVENTS.CREATED, { detail: { ok: false } }));
    });
    expect(dialog.open).toBe(true);
    expect(document.getElementById(DIALOG_IDS.form).getAttribute("aria-busy")).toBe("false");
  });

  it("closes locally on cancel without a bridge round-trip", async () => {
    const dispatchSpy = vi.spyOn(window, "dispatchEvent");
    renderIsland();
    openIslandDialog();
    await waitFor(() => expect(document.querySelector("dialog.dialog").open).toBe(true));
    const dialog = document.querySelector("dialog.dialog");
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(dialog.open).toBe(false);
    expect(
      dispatchSpy.mock.calls.some(([ev]) => ev.type === DIALOG_EVENTS.CREATE),
    ).toBe(false);
  });
});
