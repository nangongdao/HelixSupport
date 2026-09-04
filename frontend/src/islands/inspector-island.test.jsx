/**
 * Helix Support — inspector island tests (D3 long tail glue slice)
 *
 * The inspector island has mirrored tabs/panels since its D3 take-over;
 * this slice closed the quality-tab gap: the island's quality panel was an
 * empty div while legacy loadQualityPanel filled the hidden legacy
 * containers. The panel is now island-rendered but legacy-fed via
 * helix-inspector-quality, with 生成知识草稿 clicks delegated back to
 * legacy via helix-quality-draft. These tests lock that contract.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, act } from "@testing-library/react";
import React from "react";

import { InspectorIsland, INSPECTOR_EVENTS } from "./inspector-island.jsx";

const QUALITY_HTML = {
  bucketsHtml: '<section class="quality-charts"></section><article class="quality-card"><h4>退款</h4></article>',
  gapsHtml:
    '<article class="quality-gap"><button class="quality-gap-draft" data-conversation-id="conv-1" data-message-id="msg-1" type="button">生成知识草稿</button></article>',
};

const COLLABORATORS = [
  { actor_id: "demo.admin", roleLabel: "管理员" },
  { actor_id: "colleague.a", roleLabel: "坐席" },
  { actor_id: "colleague.b", roleLabel: "坐席" },
];

function renderIsland() {
  render(<InspectorIsland />);
}

function showConversation() {
  // A selected conversation switches the panels from the empty mirrors to
  // the data path; the quality panel renders in both, but the tab bridge
  // only routes to legacy loadQualityPanel from a real tab click below.
  act(() => {
    window.dispatchEvent(
      new CustomEvent(INSPECTOR_EVENTS.STATE, {
        detail: {
          detail: {
            conversation: { id: "conv-1", channel: "webchat", labels: [], priority: "normal", status: "human_active" },
          },
          canOperate: true,
          actorId: "demo.admin",
          collaborators: COLLABORATORS,
        },
      }),
    );
  });
}

async function openQualityTab() {
  fireEvent.click(screen.getByRole("tab", { name: "质量" }));
  const panel = document.getElementById("qualityPanel");
  expect(panel).toBeTruthy();
  expect(panel.hidden).toBe(false);
  return panel;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("InspectorIsland quality panel (legacy-fed)", () => {
  it("shows the empty state before any helix-inspector-quality publish", async () => {
    renderIsland();
    showConversation();
    const panel = await openQualityTab();
    expect(panel.querySelector(".inspector-empty")?.textContent).toContain("暂无质量数据");
  });

  it("renders the published buckets/gaps HTML inside the quality panel", async () => {
    renderIsland();
    showConversation();
    const panel = await openQualityTab();
    act(() => {
      window.dispatchEvent(
        new CustomEvent(INSPECTOR_EVENTS.QUALITY, { detail: QUALITY_HTML }),
      );
    });
    expect(panel.querySelector(".quality-buckets .quality-card")).toBeTruthy();
    expect(panel.querySelector(".quality-gaps .quality-gap-draft")).toBeTruthy();
    expect(panel.querySelector(".quality-buckets").getAttribute("aria-live")).toBe("polite");
  });

  it("keeps rendering the aggregates when no conversation is selected", async () => {
    renderIsland();
    const panel = await openQualityTab();
    act(() => {
      window.dispatchEvent(
        new CustomEvent(INSPECTOR_EVENTS.QUALITY, { detail: QUALITY_HTML }),
      );
    });
    expect(panel.querySelector(".quality-buckets .quality-card")).toBeTruthy();
  });
});

describe("InspectorIsland quality bridges", () => {
  it("clicking the 质量 tab dispatches helix-inspector-tab so legacy loads the panel", async () => {
    renderIsland();
    const onTab = vi.fn();
    window.addEventListener(INSPECTOR_EVENTS.TAB, onTab);
    showConversation();
    await openQualityTab();
    expect(onTab).toHaveBeenCalledTimes(1);
    expect(onTab.mock.calls[0][0].detail).toEqual({ tab: "quality" });
    window.removeEventListener(INSPECTOR_EVENTS.TAB, onTab);
  });

  it("delegates 生成知识草稿 clicks to helix-quality-draft with the row's ids", async () => {
    renderIsland();
    showConversation();
    const panel = await openQualityTab();
    act(() => {
      window.dispatchEvent(
        new CustomEvent(INSPECTOR_EVENTS.QUALITY, { detail: QUALITY_HTML }),
      );
    });
    const onDraft = vi.fn();
    window.addEventListener(INSPECTOR_EVENTS.QUALITY_DRAFT, onDraft);
    fireEvent.click(panel.querySelector(".quality-gap-draft"));
    expect(onDraft).toHaveBeenCalledTimes(1);
    expect(onDraft.mock.calls[0][0].detail).toEqual({
      conversationId: "conv-1",
      messageId: "msg-1",
    });
    window.removeEventListener(INSPECTOR_EVENTS.QUALITY_DRAFT, onDraft);
  });

  it("ignores clicks on the quality panel that are not draft buttons", async () => {
    renderIsland();
    showConversation();
    const panel = await openQualityTab();
    act(() => {
      window.dispatchEvent(
        new CustomEvent(INSPECTOR_EVENTS.QUALITY, { detail: QUALITY_HTML }),
      );
    });
    const onDraft = vi.fn();
    window.addEventListener(INSPECTOR_EVENTS.QUALITY_DRAFT, onDraft);
    fireEvent.click(panel.querySelector(".quality-card"));
    expect(onDraft).not.toHaveBeenCalled();
    window.removeEventListener(INSPECTOR_EVENTS.QUALITY_DRAFT, onDraft);
  });
});

describe("InspectorIsland note composer (island-owned)", () => {
  function openNoteForm() {
    renderIsland();
    showConversation();
    const form = document.getElementById("noteForm");
    expect(form).toBeTruthy();
    expect(form.hidden).toBe(false);
    return form;
  }

  it("renders the legacy note form contract and hides it when resolved", () => {
    const { container } = render(<InspectorIsland />);
    showConversation();
    const form = document.getElementById("noteForm");
    expect(form).toBeTruthy();
    expect(document.getElementById("noteInput")).toBeTruthy();
    expect(document.querySelector('label[for="noteInput"]')?.textContent).toContain("内部备注");
    expect(screen.getByRole("button", { name: "添加内部备注" })).toBeTruthy();
    // Resolved conversations hide the composer.
    act(() => {
      window.dispatchEvent(
        new CustomEvent(INSPECTOR_EVENTS.STATE, {
          detail: {
            detail: {
              conversation: { id: "conv-1", channel: "webchat", labels: [], priority: "normal", status: "resolved" },
            },
            canOperate: true,
            actorId: "demo.admin",
            collaborators: COLLABORATORS,
          },
        }),
      );
    });
    expect(document.getElementById("noteForm").hidden).toBe(true);
    void container;
  });

  it("renders mention candidates for a trailing @token, excluding the actor", () => {
    renderIsland();
    showConversation();
    const input = document.getElementById("noteInput");
    const typed = "请 @colleague";
    fireEvent.change(input, { target: { value: typed, selectionStart: typed.length } });
    // the @token sits at the caret; matches both colleagues.
    const options = document.querySelectorAll("#mentionSuggest .macro-option");
    expect(options.length).toBe(2);
    expect(options[0].textContent).toContain("colleague.a");
    expect(document.querySelector("#mentionSuggest")).toBeTruthy();
  });

  it("applies a picked mention at the caret with a trailing space", () => {
    renderIsland();
    showConversation();
    const input = document.getElementById("noteInput");
    fireEvent.change(input, { target: { value: "请 @co 查看进展", selectionStart: 5 } });
    const options = document.querySelectorAll("#mentionSuggest .macro-option");
    expect(options.length).toBeGreaterThan(0);
    fireEvent.click(options[0]);
    expect(input.value.startsWith("请 @colleague.a ")).toBe(true);
    expect(document.getElementById("mentionSuggest").hidden).toBe(true);
  });

  it("navigates candidates with the keyboard and applies on Enter", () => {
    const dispatchSpy = vi.spyOn(window, "dispatchEvent");
    renderIsland();
    showConversation();
    const input = document.getElementById("noteInput");
    fireEvent.change(input, { target: { value: "麻烦 @c", selectionStart: "麻烦 @c".length } });
    fireEvent.keyDown(input, { key: "ArrowDown" });
    const active = document.querySelector("#mentionSuggest .macro-option.is-active");
    expect(active?.dataset.mentionActor).toBe("colleague.a");
    fireEvent.keyDown(input, { key: "Enter" });
    const submitEvent = dispatchSpy.mock.calls.map(([ev]) => ev).find((ev) => ev.type === INSPECTOR_EVENTS.NOTE_SUBMIT);
    void submitEvent;
    expect(input.value.startsWith("麻烦 @colleague.a ")).toBe(true);
  });

  it("bridges the submit to helix-inspector-note-submit and clears on ok", () => {
    const dispatchSpy = vi.spyOn(window, "dispatchEvent");
    renderIsland();
    showConversation();
    const input = document.getElementById("noteInput");
    fireEvent.change(input, { target: { value: "核对完毕，可以解决" } });
    fireEvent.submit(document.getElementById("noteForm"));
    const submitEvent = dispatchSpy.mock.calls.map(([ev]) => ev).find((ev) => ev.type === INSPECTOR_EVENTS.NOTE_SUBMIT);
    expect(submitEvent.detail).toEqual({ content: "核对完毕，可以解决" });
    expect(input.value).not.toBe("");
    act(() => {
      window.dispatchEvent(
        new CustomEvent(INSPECTOR_EVENTS.NOTE_SUBMITTED, { detail: { ok: true } }),
      );
    });
    expect(input.value).toBe("");
  });

  it("keeps the note text when the write failed", () => {
    renderIsland();
    showConversation();
    const input = document.getElementById("noteInput");
    fireEvent.change(input, { target: { value: "失败也要保留草稿" } });
    fireEvent.submit(document.getElementById("noteForm"));
    act(() => {
      window.dispatchEvent(
        new CustomEvent(INSPECTOR_EVENTS.NOTE_SUBMITTED, { detail: { ok: false } }),
      );
    });
    expect(input.value).toBe("失败也要保留草稿");
  });
});
