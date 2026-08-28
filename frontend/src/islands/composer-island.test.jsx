/**
 * Helix Support — composer island component tests (D3)
 *
 * The composer island mirrors the customer/operator forms with a fixed DOM
 * contract (ids + aria-labels) so accessible locators keep resolving, and
 * bridges submissions back to legacy via events. These tests cover the
 * mirror + bridge contract — no legacy app.js, no network.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import React from "react";

import { ComposerIsland, COMPOSER_EVENTS, INPUT_IDS } from "../islands/composer-island.jsx";

function publishState(partial = {}) {
  act(() => {
    window.dispatchEvent(
      new CustomEvent(COMPOSER_EVENTS.STATE, {
        detail: { resolved: false, human: false, customerBusy: false, operatorBusy: false, ...partial },
      }),
    );
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ComposerIsland mirror contract", () => {
  it("renders both forms with the fixed DOM contract", () => {
    render(<ComposerIsland />);
    publishState({ human: true });
    expect(document.getElementById(INPUT_IDS.customerForm)).toBeTruthy();
    expect(document.getElementById(INPUT_IDS.operatorForm)).toBeTruthy();
    expect(document.getElementById(INPUT_IDS.customerInput)).toBeTruthy();
    // Accessible labels stay the legacy wording so get_by_label works.
    expect(screen.getByLabelText("客户消息")).toBeTruthy();
    expect(screen.getByRole("button", { name: "发送客户消息" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "发送人工回复" })).toBeTruthy();
  });

  it("hides the operator form until the conversation is human-active", () => {
    render(<ComposerIsland />);
    publishState({ human: false });
    expect(document.getElementById(INPUT_IDS.operatorForm).hidden).toBe(true);
    publishState({ human: true });
    expect(document.getElementById(INPUT_IDS.operatorForm).hidden).toBe(false);
  });

  it("disables the customer form when the conversation is resolved", () => {
    render(<ComposerIsland />);
    publishState({ resolved: true });
    expect(document.getElementById(INPUT_IDS.customerInput).disabled).toBe(true);
    expect(document.getElementById(INPUT_IDS.customerForm).querySelector("button").disabled).toBe(true);
  });

  it("sets aria-busy from the legacy busy flags", () => {
    render(<ComposerIsland />);
    publishState({ customerBusy: true });
    expect(document.getElementById(INPUT_IDS.customerForm).getAttribute("aria-busy")).toBe("true");
  });

  it("dispatches helix-composer-submit with the typed content", async () => {
    const dispatchSpy = vi.spyOn(window, "dispatchEvent");
    render(<ComposerIsland />);
    publishState({});
    const input = document.getElementById(INPUT_IDS.customerInput);
    fireEvent.change(input, { target: { value: "帮我查一下订单" } });
    fireEvent.submit(document.getElementById(INPUT_IDS.customerForm));
    const submitEvent = dispatchSpy.mock.calls.map(([ev]) => ev).find(
      (ev) => ev.type === COMPOSER_EVENTS.SUBMIT,
    );
    expect(submitEvent).toBeTruthy();
    expect(submitEvent.detail).toEqual({ kind: "customer", content: "帮我查一下订单" });
    // The mirrored textarea clears after a successful submit.
    await waitFor(() => expect(document.getElementById(INPUT_IDS.customerInput).value).toBe(""));
  });

  it("dispatches helix-composer-typing while typing in the operator input", () => {
    const dispatchSpy = vi.spyOn(window, "dispatchEvent");
    render(<ComposerIsland />);
    publishState({});
    fireEvent.change(document.getElementById(INPUT_IDS.operatorInput), {
      target: { value: "已接入" },
    });
    const typingEvent = dispatchSpy.mock.calls.map(([ev]) => ev).find(
      (ev) => ev.type === COMPOSER_EVENTS.TYPING,
    );
    expect(typingEvent).toBeTruthy();
    expect(typingEvent.detail).toEqual({ kind: "operator", content: "已接入" });
  });
});