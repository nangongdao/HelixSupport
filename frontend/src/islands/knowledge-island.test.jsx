/**
 * Helix Support — knowledge island component tests (D3)
 *
 * The island owns the whole knowledge surface including the draft editor, but
 * every write bridges back to legacy via events. These tests cover the reducer
 * triplet, the validation parity with legacy saveKnowledgeArticle, and the
 * bridge contract — no legacy app.js, only a stubbed fetch.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";

import {
  KnowledgeIsland,
  KNOWLEDGE_EVENTS,
  EDITOR_IDS,
  createKnowledgeState,
  reduceKnowledge,
  validateKnowledgeDraft,
} from "./knowledge-island.jsx";

function makeArticle(overrides = {}) {
  return {
    id: "kb_1",
    title: "退款政策",
    content: "下单后 7 天内可申请全额退款。",
    tags: ["refund", "policy"],
    category: "billing",
    language: "zh",
    source_url: "internal:policy/refund",
    status: "draft",
    version: 2,
    updated_at: "2026-08-29T02:00:00Z",
    ...overrides,
  };
}

/** The island reads the role off window, the same way legacy publishes it. */
function stubBackend(articles, role = "admin") {
  window.__HELIX_ROLE__ = role;
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({ ok: true, status: 200, json: async () => articles })),
  );
}

async function renderIsland(articles = [makeArticle()], role = "admin") {
  stubBackend(articles, role);
  // retry: false keeps a rejected query from stalling the error assertions.
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <KnowledgeIsland />
    </QueryClientProvider>,
  );
  await waitFor(() => expect(document.querySelector(".knowledge-island")).toBeTruthy());
  return { articles, client };
}

function openEditorForFirstArticle() {
  fireEvent.click(screen.getByRole("button", { name: "编辑" }));
}

beforeEach(() => {
  delete window.__HELIX_ROLE__;
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("reduceKnowledge editor lifecycle", () => {
  it("opens a blank draft and a prefilled edit from the same action", () => {
    const blank = reduceKnowledge(createKnowledgeState(), { type: "OPEN_EDITOR", article: null });
    expect(blank.editor.articleId).toBeNull();
    expect(blank.editor.values.category).toBe("general");

    const article = { ...makeArticle(), tags: ["refund", "policy"] };
    const editing = reduceKnowledge(createKnowledgeState(), { type: "OPEN_EDITOR", article });
    expect(editing.editor.articleId).toBe("kb_1");
    // Legacy editKnowledgeArticle joins tags with ", " for the text input.
    expect(editing.editor.values.tags).toBe("refund, policy");
    expect(editing.editor.values.sourceUrl).toBe("internal:policy/refund");
  });

  it("RESET_EDITOR drops the editing id, matching legacy resetKnowledgeEditor", () => {
    const open = reduceKnowledge(createKnowledgeState(), {
      type: "OPEN_EDITOR",
      article: makeArticle(),
    });
    const reset = reduceKnowledge(open, { type: "RESET_EDITOR" });
    expect(reset.editor.articleId).toBeNull();
    expect(reset.editor.values.title).toBe("");
  });

  it("ignores field/busy actions while the editor is closed", () => {
    const closed = createKnowledgeState();
    expect(reduceKnowledge(closed, { type: "SET_FIELD", name: "title", value: "x" })).toBe(closed);
    expect(reduceKnowledge(closed, { type: "SET_EDITOR_BUSY", busy: true })).toBe(closed);
  });

  it("keeps filters independent of the editor", () => {
    const filtered = reduceKnowledge(createKnowledgeState(), {
      type: "SET_FILTER",
      payload: { status: "draft" },
    });
    const opened = reduceKnowledge(filtered, { type: "OPEN_EDITOR", article: null });
    expect(opened.filters.status).toBe("draft");
    expect(reduceKnowledge(opened, { type: "CLOSE_EDITOR" }).filters.status).toBe("draft");
  });
});

describe("validateKnowledgeDraft parity with legacy", () => {
  const base = { title: "标题", content: "内容内容内容内容内容", tags: ["a"] };

  it("reports tags before title before content", () => {
    expect(validateKnowledgeDraft({ ...base, tags: [] }).field).toBe("tags");
    expect(validateKnowledgeDraft({ ...base, title: "短" }).field).toBe("title");
    expect(validateKnowledgeDraft({ ...base, content: "太短" }).field).toBe("content");
    expect(validateKnowledgeDraft(base)).toBeNull();
  });

  it("keeps the legacy copy", () => {
    expect(validateKnowledgeDraft({ ...base, tags: [] }).message).toBe("请至少填写一个知识标签");
    expect(validateKnowledgeDraft({ ...base, title: "短" }).message).toBe("标题至少需要 2 个字符");
    expect(validateKnowledgeDraft({ ...base, content: "太短" }).message).toBe("正文至少需要 10 个字符");
  });
});

describe("KnowledgeIsland editor surface", () => {
  it("keeps the editor aside in the DOM but hidden until opened", async () => {
    await renderIsland();
    const aside = document.querySelector(".knowledge-editor");
    // The single-column layout rule keys off .knowledge-editor[hidden], so the
    // aside must exist even when closed.
    expect(aside).toBeTruthy();
    expect(aside.hidden).toBe(true);
    expect(document.getElementById(EDITOR_IDS.form)).toBeNull();

    openEditorForFirstArticle();
    expect(document.querySelector(".knowledge-editor").hidden).toBe(false);
    expect(document.getElementById(EDITOR_IDS.form)).toBeTruthy();
  });

  it("prefills the form from the article and focuses the title", async () => {
    await renderIsland();
    openEditorForFirstArticle();
    expect(document.getElementById(EDITOR_IDS.title).value).toBe("退款政策");
    expect(document.getElementById(EDITOR_IDS.tags).value).toBe("refund, policy");
    expect(document.getElementById(EDITOR_IDS.category).value).toBe("billing");
    expect(document.getElementById(EDITOR_IDS.language).value).toBe("zh");
    expect(screen.getByRole("heading", { name: "编辑知识文章" })).toBeTruthy();
    expect(document.activeElement.id).toBe(EDITOR_IDS.title);
  });

  it("opens a blank draft on the legacy helix-knowledge-new event", async () => {
    await renderIsland();
    act(() => {
      window.dispatchEvent(new CustomEvent(KNOWLEDGE_EVENTS.NEW));
    });
    expect(screen.getByRole("heading", { name: "新建知识草稿" })).toBeTruthy();
    expect(document.getElementById(EDITOR_IDS.title).value).toBe("");
    expect(document.getElementById(EDITOR_IDS.category).value).toBe("general");
  });

  it("offers every app.js language so editing never drops an unlisted one", async () => {
    await renderIsland([makeArticle({ language: "ar" })]);
    openEditorForFirstArticle();
    const select = document.getElementById(EDITOR_IDS.language);
    expect(select.value).toBe("ar");
    const codes = [...select.options].map((o) => o.value);
    for (const code of ["zh", "en", "ja", "ko", "ru", "ar", "hi", "he", "th", "el", "es", "fr", "de", "pt"]) {
      expect(codes).toContain(code);
    }
  });

  it("hides the editor and the write actions for a reader role", async () => {
    await renderIsland([makeArticle({ status: "published" })], "viewer");
    expect(screen.queryByRole("button", { name: "编辑" })).toBeNull();
    expect(document.querySelector(".knowledge-editor").hidden).toBe(true);
    expect(screen.getByText(/仅对知识管理员开放/)).toBeTruthy();
  });

  it("requests inactive articles for a writer", async () => {
    await renderIsland();
    expect(fetch).toHaveBeenCalledWith("/api/knowledge?include_inactive=true", expect.anything());
  });

  it("requests only published articles for a reader", async () => {
    await renderIsland([makeArticle({ status: "published" })], "viewer");
    expect(fetch).toHaveBeenCalledWith("/api/knowledge", expect.anything());
  });
});

describe("KnowledgeIsland bridge contract", () => {
  it("dispatches helix-knowledge-save with the trimmed payload", async () => {
    await renderIsland();
    openEditorForFirstArticle();
    const dispatchSpy = vi.spyOn(window, "dispatchEvent");
    fireEvent.change(document.getElementById(EDITOR_IDS.title), {
      target: { value: "  退款政策 v2  " },
    });
    fireEvent.change(document.getElementById(EDITOR_IDS.tags), {
      target: { value: "refund, refund, policy" },
    });
    fireEvent.submit(document.getElementById(EDITOR_IDS.form));

    const saveEvent = dispatchSpy.mock.calls
      .map(([ev]) => ev)
      .find((ev) => ev.type === KNOWLEDGE_EVENTS.SAVE);
    expect(saveEvent).toBeTruthy();
    expect(saveEvent.detail.editingId).toBe("kb_1");
    expect(saveEvent.detail.payload.title).toBe("退款政策 v2");
    // parseKnowledgeTags dedupes, matching the legacy payload projection.
    expect(saveEvent.detail.payload.tags).toEqual(["refund", "policy"]);
    expect(document.getElementById(EDITOR_IDS.form).getAttribute("aria-busy")).toBe("true");
  });

  it("blocks the save and surfaces the legacy message when tags are empty", async () => {
    await renderIsland();
    openEditorForFirstArticle();
    const dispatchSpy = vi.spyOn(window, "dispatchEvent");
    fireEvent.change(document.getElementById(EDITOR_IDS.tags), { target: { value: "  " } });
    fireEvent.submit(document.getElementById(EDITOR_IDS.form));

    expect(
      dispatchSpy.mock.calls.map(([ev]) => ev).some((ev) => ev.type === KNOWLEDGE_EVENTS.SAVE),
    ).toBe(false);
    expect(screen.getByRole("alert").textContent).toBe("请至少填写一个知识标签");
    expect(document.activeElement.id).toBe(EDITOR_IDS.tags);
  });

  it("closes the editor once legacy reports a successful save", async () => {
    await renderIsland();
    openEditorForFirstArticle();
    fireEvent.submit(document.getElementById(EDITOR_IDS.form));
    act(() => {
      window.dispatchEvent(new CustomEvent(KNOWLEDGE_EVENTS.SAVED, { detail: { ok: true } }));
    });
    await waitFor(() => expect(document.querySelector(".knowledge-editor").hidden).toBe(true));
  });

  it("keeps the editor open and editable when legacy reports a failure", async () => {
    await renderIsland();
    openEditorForFirstArticle();
    fireEvent.submit(document.getElementById(EDITOR_IDS.form));
    act(() => {
      window.dispatchEvent(new CustomEvent(KNOWLEDGE_EVENTS.SAVED, { detail: { ok: false } }));
    });
    expect(document.querySelector(".knowledge-editor").hidden).toBe(false);
    // Busy clears so the operator can retry instead of a stuck submit button.
    expect(document.getElementById(EDITOR_IDS.form).getAttribute("aria-busy")).toBe("false");
  });

  it("bridges publish/retire to legacy instead of handling them locally", async () => {
    await renderIsland();
    const dispatchSpy = vi.spyOn(window, "dispatchEvent");
    fireEvent.click(screen.getByRole("button", { name: "发布" }));
    const actionEvent = dispatchSpy.mock.calls
      .map(([ev]) => ev)
      .find((ev) => ev.type === KNOWLEDGE_EVENTS.ACTION);
    expect(actionEvent.detail).toEqual({ action: "publish", articleId: "kb_1" });
    // Edit stays island-local — no bridge event for it.
    expect(document.querySelector(".knowledge-editor").hidden).toBe(true);
  });

  it("refetches on a forced helix-knowledge-refresh", async () => {
    await renderIsland();
    const before = fetch.mock.calls.length;
    act(() => {
      window.dispatchEvent(
        new CustomEvent(KNOWLEDGE_EVENTS.REFRESH, { detail: { force: true } }),
      );
    });
    await waitFor(() => expect(fetch.mock.calls.length).toBeGreaterThan(before));
  });

  it("skips the round-trip when an unforced refresh finds fresh data", async () => {
    // Re-opening the knowledge view dispatches an unforced refresh. The island
    // mounts at page load, so without this the view would fetch twice.
    await renderIsland();
    const before = fetch.mock.calls.length;
    act(() => {
      window.dispatchEvent(new CustomEvent(KNOWLEDGE_EVENTS.REFRESH));
    });
    await waitFor(() => expect(document.querySelector(".knowledge-island")).toBeTruthy());
    expect(fetch.mock.calls.length).toBe(before);
  });

  it("still refetches an unforced refresh once the data went stale", async () => {
    const { client } = await renderIsland();
    // refetchType none marks the query stale without fetching, which is what
    // an expired staleTime looks like — the legacy 15s cache expiring.
    await act(async () => {
      await client.invalidateQueries({ refetchType: "none" });
    });
    const before = fetch.mock.calls.length;
    act(() => {
      window.dispatchEvent(new CustomEvent(KNOWLEDGE_EVENTS.REFRESH));
    });
    await waitFor(() => expect(fetch.mock.calls.length).toBeGreaterThan(before));
  });
});
