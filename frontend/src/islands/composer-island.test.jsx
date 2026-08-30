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

const CANNED = [
  { id: "macro-1", title: "催单回复", shortcut: "cuidan", body: "您的订单正在加急处理。" },
  { id: "macro-2", title: "退款指引", shortcut: "tuikuan", body: "退款将在 3 个工作日内到账。" },
];

function publishToolsState(partial = {}) {
  publishState({
    human: true,
    canOperate: true,
    cannedResponses: CANNED,
    pendingAttachments: [],
    ...partial,
  });
}

function publishCopilot(detail) {
  act(() => {
    window.dispatchEvent(new CustomEvent(COMPOSER_EVENTS.COPILOT, { detail }));
  });
}

describe("ComposerIsland canned bar and macro suggest", () => {
  it("hides the tool surfaces until the conversation is human + canOperate", () => {
    render(<ComposerIsland />);
    publishState({ human: true, canOperate: false, cannedResponses: CANNED });
    expect(document.getElementById("cannedBar").hidden).toBe(true);
    expect(document.getElementById("copilotBar").hidden).toBe(true);
    expect(document.getElementById("attachmentBar").hidden).toBe(true);
    publishToolsState();
    expect(document.getElementById("cannedBar").hidden).toBe(false);
    expect(document.getElementById("copilotBar").hidden).toBe(false);
    expect(document.getElementById("attachmentBar").hidden).toBe(false);
  });

  it("renders canned chips and bridges usage tracking with island-local insertion", () => {
    const dispatchSpy = vi.spyOn(window, "dispatchEvent");
    render(<ComposerIsland />);
    publishToolsState();
    const chips = document.querySelectorAll("#cannedList .canned-chip");
    expect(chips.length).toBe(2);
    expect(chips[0].textContent).toContain("催单回复");
    fireEvent.click(chips[0]);
    const useEvent = dispatchSpy.mock.calls.map(([ev]) => ev).find((ev) => ev.type === COMPOSER_EVENTS.MACRO_USE);
    expect(useEvent.detail).toEqual({ macroId: "macro-1" });
    expect(document.getElementById(INPUT_IDS.operatorInput).value).toBe("您的订单正在加急处理。");
  });

  it("shows macro suggest for a trailing /token and replaces it on pick", () => {
    const dispatchSpy = vi.spyOn(window, "dispatchEvent");
    render(<ComposerIsland />);
    publishToolsState();
    const input = document.getElementById(INPUT_IDS.operatorInput);
    fireEvent.change(input, { target: { value: "请稍等，我发您 /tui" } });
    const options = document.querySelectorAll("#macroSuggest .macro-option");
    expect(options.length).toBe(1);
    expect(options[0].textContent).toContain("退款指引");
    fireEvent.click(options[0]);
    expect(input.value).toBe("请稍等，我发您 退款将在 3 个工作日内到账。");
    const useEvent = dispatchSpy.mock.calls.map(([ev]) => ev).find((ev) => ev.type === COMPOSER_EVENTS.MACRO_USE);
    expect(useEvent.detail).toEqual({ macroId: "macro-2" });
  });

  it("keeps the macro suggest hidden without a trailing /token", () => {
    render(<ComposerIsland />);
    publishToolsState();
    fireEvent.change(document.getElementById(INPUT_IDS.operatorInput), {
      target: { value: "普通文本没有建议" },
    });
    expect(document.getElementById("macroSuggest").hidden).toBe(true);
  });
});

describe("ComposerIsland copilot tools", () => {
  it("dispatches helix-composer-copilot-suggest with the island draft", () => {
    const dispatchSpy = vi.spyOn(window, "dispatchEvent");
    render(<ComposerIsland />);
    publishToolsState();
    fireEvent.change(document.getElementById(INPUT_IDS.operatorInput), {
      target: { value: "客户催单，帮我起草" },
    });
    fireEvent.click(document.getElementById("copilotSuggestBtn"));
    const suggestEvent = dispatchSpy.mock.calls.map(([ev]) => ev).find(
      (ev) => ev.type === COMPOSER_EVENTS.COPILOT_SUGGEST,
    );
    expect(suggestEvent.detail).toEqual({ draft: "客户催单，帮我起草" });
  });

  it("renders published suggestions and applies one into the textarea", () => {
    render(<ComposerIsland />);
    publishToolsState();
    publishCopilot({
      suggestions: [
        { content: "建议回复一", source: "model" },
        { content: "建议回复二", source: "canned" },
      ],
      status: "",
    });
    const items = document.querySelectorAll("#copilotSuggestions .copilot-suggestion");
    expect(items.length).toBe(2);
    expect(items[0].querySelector(".copilot-suggestion-badge").textContent).toBe("AI");
    fireEvent.click(items[1]);
    expect(document.getElementById(INPUT_IDS.operatorInput).value).toBe("建议回复二");
  });

  it("bridges the tone rewrite and lands the rewritten text in the textarea", () => {
    const dispatchSpy = vi.spyOn(window, "dispatchEvent");
    render(<ComposerIsland />);
    publishToolsState();
    fireEvent.change(document.getElementById(INPUT_IDS.operatorInput), {
      target: { value: "原始草稿" },
    });
    fireEvent.change(document.getElementById("copilotTone"), { target: { value: "concise" } });
    const toneEvent = dispatchSpy.mock.calls.map(([ev]) => ev).find(
      (ev) => ev.type === COMPOSER_EVENTS.COPILOT_TONE,
    );
    expect(toneEvent.detail).toEqual({ tone: "concise", text: "原始草稿" });
    publishCopilot({ rewritten: "改写后的草稿", status: "已改写" });
    expect(document.getElementById(INPUT_IDS.operatorInput).value).toBe("改写后的草稿");
    expect(document.getElementById("copilotStatus").textContent).toBe("已改写");
    // The select resets to its placeholder after a rewrite request.
    expect(document.getElementById("copilotTone").value).toBe("");
  });

  it("renders copilot knowledge articles and applies a title on click", () => {
    render(<ComposerIsland />);
    publishToolsState();
    publishCopilot({ knowledge: [{ title: "配送时效", category: "物流" }] });
    const articles = document.querySelectorAll("#copilotKnowledge .copilot-kb-item");
    expect(articles.length).toBe(1);
    fireEvent.click(articles[0]);
    expect(document.getElementById(INPUT_IDS.operatorInput).value).toBe("配送时效");
  });
});

describe("ComposerIsland attachment bar", () => {
  it("renders pending chips from the state snapshot and bridges removal", () => {
    const dispatchSpy = vi.spyOn(window, "dispatchEvent");
    render(<ComposerIsland />);
    publishToolsState({
      pendingAttachments: [{ id: "att-1", filename: "发货单.pdf" }],
    });
    const chips = document.querySelectorAll("#pendingAttachments .pending-attachment-chip");
    expect(chips.length).toBe(1);
    expect(chips[0].textContent).toContain("发货单.pdf");
    fireEvent.click(chips[0].querySelector(".pending-attachment-remove"));
    const removeEvent = dispatchSpy.mock.calls.map(([ev]) => ev).find(
      (ev) => ev.type === COMPOSER_EVENTS.ATTACHMENT_REMOVE,
    );
    expect(removeEvent.detail).toEqual({ id: "att-1" });
  });

  it("bridges a selected file to helix-composer-attachment-upload", () => {
    const dispatchSpy = vi.spyOn(window, "dispatchEvent");
    render(<ComposerIsland />);
    publishToolsState();
    const file = new File(["hello"], "报告.pdf", { type: "application/pdf" });
    fireEvent.change(document.getElementById("attachmentFile"), { target: { files: [file] } });
    const uploadEvent = dispatchSpy.mock.calls.map(([ev]) => ev).find(
      (ev) => ev.type === COMPOSER_EVENTS.ATTACHMENT_UPLOAD,
    );
    expect(uploadEvent.detail.file).toBe(file);
  });
});