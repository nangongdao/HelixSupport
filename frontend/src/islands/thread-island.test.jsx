/**
 * Helix Support — thread island tests (D3 long tail glue slice)
 *
 * The transcript is island-rendered but legacy-fed: js/thread.js publishes
 * helix-thread-state snapshots and owns the feedback/translate writes via
 * the helix-thread-* bridges. These tests lock the render mirror and the
 * bridge contracts.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, act } from "@testing-library/react";
import React from "react";

import { ThreadIsland, THREAD_EVENTS } from "./thread-island.jsx";

const MESSAGES = [
  {
    id: "msg-1",
    role: "customer",
    content: "配送一般多久能到？",
    created_at: "2026-08-30T08:00:00Z",
  },
  {
    id: "msg-2",
    role: "assistant",
    content: "根据当前服务政策 48 小时内送达 @duty.lead",
    created_at: "2026-08-30T08:00:05Z",
    metadata: { agent: "knowledge", attachment_ids: [] },
  },
];

const SNAPSHOT = {
  loading: false,
  messages: MESSAGES,
  threadPrevCursor: null,
  lowPerf: false,
  canTranslate: true,
  preserveAnchor: false,
  languages: [
    { code: "en", name: "English" },
    { code: "ja", name: "日本語" },
  ],
  attachmentMeta: {},
};

function renderIsland() {
  const container = document.createElement("div");
  container.dataset.threadTestRoot = "1";
  document.body.appendChild(container);
  render(<ThreadIsland container={container} />, { container });
  return container;
}

function publish(detail) {
  act(() => {
    window.dispatchEvent(new CustomEvent(THREAD_EVENTS.STATE, { detail }));
  });
}

afterEach(() => {
  cleanup();
  container_remover();
  vi.restoreAllMocks();
});

function container_remover() {
  document.querySelectorAll("[data-thread-test-root]").forEach((node) => node.remove());
}

describe("ThreadIsland state rendering", () => {
  it("renders nothing before the first snapshot", () => {
    const container = renderIsland();
    expect(container.children.length).toBe(0);
  });

  it("shows the loading state from a loading snapshot", () => {
    renderIsland();
    publish({ ...SNAPSHOT, loading: true, messages: [] });
    expect(screen.getByText("正在加载会话")).toBeTruthy();
  });

  it("shows the empty state when the conversation has no messages", () => {
    renderIsland();
    publish({ ...SNAPSHOT, messages: [] });
    expect(screen.getByText("等待第一条客户消息")).toBeTruthy();
  });

  it("mirrors the legacy transcript DOM for a loaded thread", () => {
    renderIsland();
    publish(SNAPSHOT);
    const rows = document.querySelectorAll(".message-row");
    expect(rows.length).toBe(2);
    expect(rows[0].className).toContain("customer");
    expect(rows[1].className).toContain("assistant");
    expect(document.querySelector(".agent-chip")?.textContent).toBe("knowledge");
    expect(document.querySelector(".mention-chip")?.textContent).toBe("@duty.lead");
    expect(document.querySelector(".message-meta time").textContent).toMatch(/\d{2}:\d{2}/);
  });

  it("shows the load-older affordance only with a live cursor", () => {
    renderIsland();
    publish(SNAPSHOT);
    expect(document.querySelector(".thread-load-older-btn")).toBeNull();
    publish({ ...SNAPSHOT, threadPrevCursor: "cursor-1" });
    expect(document.querySelector(".thread-load-older-btn")).toBeTruthy();
  });

  it("truncates in low-perf mode with the legacy notice", () => {
    renderIsland();
    const many = Array.from({ length: 90 }, (_, i) => ({
      id: `m-${i}`,
      role: "customer",
      content: `第 ${i} 条`,
      created_at: "2026-08-30T08:00:00Z",
    }));
    publish({ ...SNAPSHOT, lowPerf: true, messages: many });
    expect(document.querySelectorAll(".message-row").length).toBe(80);
    expect(document.querySelector(".thread-empty")?.textContent).toContain("仅显示最近 80 条");
  });
});

describe("ThreadIsland bridges", () => {
  it("dispatches helix-thread-feedback with the row's ids and mirrors is-recorded", () => {
    renderIsland();
    publish(SNAPSHOT);
    const onFeedback = vi.fn();
    window.addEventListener(THREAD_EVENTS.FEEDBACK, onFeedback);
    const helpful = document.querySelector('.feedback-button[data-feedback="1"]');
    fireEvent.click(helpful);
    expect(onFeedback).toHaveBeenCalledTimes(1);
    expect(onFeedback.mock.calls[0][0].detail).toEqual({ messageId: "msg-2", rating: 1 });
    expect(helpful.classList.contains("is-recorded")).toBe(false);
    act(() => {
      window.dispatchEvent(
        new CustomEvent(THREAD_EVENTS.FEEDBACK_RECORDED, {
          detail: { messageId: "msg-2", rating: 1, ok: true },
        }),
      );
    });
    const recorded = document.querySelector('.feedback-button[data-feedback="1"]');
    expect(recorded.classList.contains("is-recorded")).toBe(true);
    expect(recorded.getAttribute("aria-pressed")).toBe("true");
    expect(recorded.getAttribute("title")).toBe("已记录");
    window.removeEventListener(THREAD_EVENTS.FEEDBACK, onFeedback);
  });

  it("does not mark feedback recorded when the write failed", () => {
    renderIsland();
    publish(SNAPSHOT);
    act(() => {
      window.dispatchEvent(
        new CustomEvent(THREAD_EVENTS.FEEDBACK_RECORDED, {
          detail: { messageId: "msg-2", rating: -1, ok: false },
        }),
      );
    });
    expect(document.querySelectorAll(".feedback-button.is-recorded").length).toBe(0);
  });

  it("dispatches helix-thread-translate and renders the legacy-built result", () => {
    renderIsland();
    publish(SNAPSHOT);
    const onTranslate = vi.fn();
    window.addEventListener(THREAD_EVENTS.TRANSLATE, onTranslate);
    const bar = document.querySelector(".translate-bar");
    expect(bar?.dataset.messageId).toBe("msg-1");
    fireEvent.change(bar.querySelector(".translate-lang"), { target: { value: "ja" } });
    fireEvent.click(bar.querySelector(".translate-button"));
    expect(onTranslate).toHaveBeenCalledTimes(1);
    expect(onTranslate.mock.calls[0][0].detail).toEqual({ messageId: "msg-1", language: "ja" });
    act(() => {
      window.dispatchEvent(
        new CustomEvent(THREAD_EVENTS.TRANSLATE_RESULT, {
          detail: {
            messageId: "msg-1",
            html: '<span class="translate-text">いつ届きますか</span><span class="translate-tag">已翻译为 日本語</span>',
          },
        }),
      );
    });
    const result = bar.querySelector(".translate-result");
    expect(result.querySelector(".translate-text")?.textContent).toBe("いつ届きますか");
    expect(result.querySelector(".translate-tag")?.textContent).toBe("已翻译为 日本語");
    window.removeEventListener(THREAD_EVENTS.TRANSLATE, onTranslate);
  });

  it("hides the translate bars when the actor cannot write", () => {
    renderIsland();
    publish({ ...SNAPSHOT, canTranslate: false });
    expect(document.querySelector(".translate-bar")).toBeNull();
  });

  it("dispatches helix-thread-load-older from the affordance button", () => {
    renderIsland();
    publish({ ...SNAPSHOT, threadPrevCursor: "cursor-9" });
    const onLoadOlder = vi.fn();
    window.addEventListener(THREAD_EVENTS.LOAD_OLDER, onLoadOlder);
    fireEvent.click(document.querySelector(".thread-load-older-btn"));
    expect(onLoadOlder).toHaveBeenCalledTimes(1);
    window.removeEventListener(THREAD_EVENTS.LOAD_OLDER, onLoadOlder);
  });
});
