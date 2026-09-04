// Helix Support — conversation detail lifecycle unit tests (app.js <500)
// Run: node --test tests/frontend/conversation-detail.test.js

import { afterEach, beforeEach, test } from "node:test";
import assert from "node:assert/strict";

import {
  configure,
  clearSelection,
  ensureSelectedRowVisible,
  loadDetail,
  renderLanguagePicker,
  renderSubtitle,
  selectConversation,
} from "../../app/static/js/conversation-detail.js";

function makeEl(extra = {}) {
  return {
    value: "",
    textContent: "",
    innerHTML: "",
    hidden: true,
    scrollTop: 0,
    clientHeight: 500,
    dataset: {},
    classList: { contains: () => false, toggle() {} },
    insertAdjacentHTML() {},
    ...extra,
  };
}

function configureDeps(overrides = {}) {
  const els = {
    conversationSubtitle: makeEl(),
    conversationLanguageSelect: makeEl({ dataset: {} }),
    emptyState: makeEl(),
    conversationView: makeEl(),
    messages: makeEl(),
    queuePane: makeEl({ classList: { contains: () => false } }),
    noteForm: makeEl(),
    list: makeEl({ scrollTop: 100, clientHeight: 500 }),
  };
  const calls = [];
  const ctx = {
    state: {
      detailSequence: 0,
      selectedId: null,
      detail: null,
      lowPerf: false,
      threadPrevCursor: null,
      threadLoadingOlder: false,
      queueVirtual: false,
      conversations: [],
    },
    els,
    apiWithHeaders: async () => {
      calls.push({ api: true });
      return {
        response: { headers: { get: (h) => (h === "X-Prev-Cursor" ? "cursor-9" : null) } },
        data: { id: "c1" },
      };
    },
    renderDetail: (d) => calls.push({ renderDetail: d }),
    renderQueue: () => calls.push({ renderQueue: true }),
    stopWatching: () => calls.push({ stopWatching: true }),
    resetCopilot: () => calls.push({ resetCopilot: true }),
    hideMentionSuggest: () => calls.push({ hideMentionSuggest: true }),
    windowedRowHeight: () => 118,
    closeQueueDrawer: (opts) => calls.push({ closeQueueDrawer: opts }),
    canWriteConversations: () => true,
    showToast: (msg, err) => calls.push({ toast: msg, err }),
    languageNames: { zh: "中文", en: "英文" },
    languageOptions: '<option value="zh">中文</option>',
    threadPageLimit: 100,
    ...overrides,
  };
  configure(ctx);
  return { ctx, calls };
}

let originalWindow;
beforeEach(() => {
  originalWindow = globalThis.window;
  globalThis.window = { __HELIX_ISLAND_MODE__: false };
});
afterEach(() => {
  configure(null);
  if (originalWindow === undefined) delete globalThis.window;
  else globalThis.window = originalWindow;
});

test("renderSubtitle joins id/channel/customer with the language name", () => {
  const { ctx } = configureDeps();
  renderSubtitle({ id: "c1", channel: "web", customer_ref: "张三", language: "zh" });
  assert.equal(ctx.els.conversationSubtitle.textContent, "c1 · web · 张三 · 语言:中文");
});

test("renderSubtitle uses 未绑定身份 when no customer ref", () => {
  const { ctx } = configureDeps();
  renderSubtitle({ id: "c1", channel: "web", language: null });
  assert.equal(ctx.els.conversationSubtitle.textContent, "c1 · web · 未绑定身份");
});

test("renderLanguagePicker builds options once and mirrors the language", () => {
  const { ctx, calls } = configureDeps();
  renderLanguagePicker({ language: "en" });
  const sel = ctx.els.conversationLanguageSelect;
  assert.equal(sel.hidden, false);
  assert.equal(sel.dataset.built, "1");
  assert.equal(sel.value, "en");
  assert.equal(calls.length, 0, "no API calls from the picker");
});

test("loadDetail fetches the tail with the prev-cursor and hands off to renderDetail", async () => {
  const { ctx, calls } = configureDeps();
  ctx.state.selectedId = "c1";
  ctx.state.detailSequence = 0;
  const ok = await loadDetail("c1");
  assert.equal(ok, true);
  assert.equal(ctx.state.threadPrevCursor, "cursor-9");
  assert.equal(ctx.state.threadLoadingOlder, false);
  const rd = calls.find((c) => c.renderDetail);
  assert.equal(rd.renderDetail.id, "c1");
});

test("loadDetail bails out when the selection changed mid-flight", async () => {
  const { ctx, calls } = configureDeps();
  ctx.state.selectedId = "c2"; // selection changed
  ctx.state.detailSequence = 1; // sequence advanced too
  const ok = await loadDetail("c1");
  assert.equal(ok, false);
  assert.equal(calls.filter((c) => c.renderDetail).length, 0);
});

test("selectConversation loads the detail and closes an open queue drawer", async () => {
  const { ctx, calls } = configureDeps();
  ctx.els.queuePane.classList.contains = () => true;
  await selectConversation("c1");
  assert.equal(ctx.state.selectedId, "c1");
  assert.equal(calls.filter((c) => c.hideMentionSuggest).length, 1);
  assert.equal(calls.filter((c) => c.renderQueue).length, 1);
  assert.equal(calls.filter((c) => c.stopWatching).length, 1);
  assert.ok(calls.some((c) => c.closeQueueDrawer));
});

test("selectConversation clears the selection on a load error", async () => {
  const { calls } = configureDeps({
    apiWithHeaders: async () => {
      throw new Error("boom");
    },
  });
  await selectConversation("c1");
  assert.ok(calls.some((c) => c.toast && c.err));
  assert.ok(calls.some((c) => c.resetCopilot), "clearSelection resets copilot");
});

test("ensureSelectedRowVisible nudges the list scrollport in virtual mode", () => {
  const { ctx } = configureDeps();
  ctx.state.queueVirtual = true;
  ctx.state.conversations = [{ id: "a" }, { id: "b" }, { id: "c" }];
  // target index 2 at rowHeight 118 -> top 236, bottom 354, viewport 100..600
  ensureSelectedRowVisible("c");
  assert.equal(ctx.els.list.scrollTop, 100, "already inside the viewport");
});

test("clearSelection resets state and paints the empty view", () => {
  const { ctx, calls } = configureDeps();
  ctx.state.selectedId = "c1";
  ctx.state.detail = { id: "c1" };
  clearSelection();
  assert.equal(ctx.state.selectedId, null);
  assert.equal(ctx.state.detail, null);
  assert.equal(ctx.els.emptyState.hidden, false);
  assert.equal(ctx.els.conversationView.hidden, true);
  assert.equal(ctx.els.noteForm.hidden, true);
  assert.ok(calls.some((c) => c.resetCopilot));
});
