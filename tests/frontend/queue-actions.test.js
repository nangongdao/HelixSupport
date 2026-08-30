// Helix Support — queue pagination & bulk actions tests (app.js <500 slice 24)
// Run: node --test tests/frontend/queue-actions.test.js

import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";

import {
  applyBulkAction,
  bindQueueActions,
  configure,
  loadMoreConversations,
} from "../../app/static/js/queue-actions.js";

function stubEls() {
  const el = () => ({
    value: "",
    textContent: "",
    innerHTML: "",
    hidden: false,
    disabled: false,
    listeners: {},
    focus() {},
    addEventListener(name, handler) { this.listeners[name] = handler; },
  });
  return {
    loadMore: el(),
    applyBulk: el(),
    bulkAction: el(),
    bulkLabelInput: el(),
    bulkToolbar: el(),
    ownershipFilter: el(),
    channelFilter: el(),
    sortFilter: el(),
    statusFilter: el(),
    labelFilter: el(),
    priorityFilter: el(),
    searchInput: el(),
  };
}

function configureDeps({ apiImpl, overrides = {} } = {}) {
  const els = stubEls();
  const calls = [];
  const state = {
    selectedId: null,
    bulkSelected: new Set(["conv-1", "conv-2"]),
    queueHasMore: true,
    queueCursor: "cursor-1",
    queueLoadingMore: false,
    labelsLoadedAt: 10,
    conversations: [],
    ...(overrides.state || {}),
  };
  const actions = {
    conversationQuery: () => "limit=20",
    renderQueue: () => calls.push("renderQueue"),
    renderBulkToolbar: () => calls.push("renderBulkToolbar"),
  };
  configure({
    state,
    els,
    api: apiImpl || (async (url, options) => {
      calls.push({ url, options });
      return { updated: 2 };
    }),
    apiWithHeaders: async (url) => {
      calls.push({ url });
      return {
        data: [{ id: "conv-3", version: 1, updated_at: "t" }],
        response: { headers: { get: (name) => (name === "X-Has-More" ? "false" : null) } },
      };
    },
    showToast: async (message, isError) => calls.push({ toast: message, isError }),
    setFormBusy: (form, busy) => calls.push({ busy }),
    refreshAll: async (options) => calls.push({ refreshAll: options }),
    actions,
    ...overrides,
    state, // overrides.state merges into the base state, never replaces it
  });
  return { calls, els, state, actions };
}

beforeEach(() => {
  delete globalThis.window;
  delete globalThis.document;
});

test("loadMoreConversations appends the next page and stops when exhausted", async () => {
  globalThis.window = new (class extends EventTarget {})();
  const { calls, state } = configureDeps({
    apiImpl: async (url) => ({
      data: [{ id: "conv-3", version: 1, updated_at: "t" }],
      response: { headers: { get: (name) => (name === "X-Has-More" ? "false" : null) } },
    }),
  });
  await loadMoreConversations();
  assert.ok(calls.includes("renderQueue"));
  assert.equal(state.conversations.length, 1);
  assert.equal(state.queueHasMore, false);
  assert.equal(state.queueCursor, null);
  assert.equal(state.queueLoadingMore, false);
  // Exhausted: a second call is a no-op.
  await loadMoreConversations();
});

test("loadMoreConversations skips a stale query key", async () => {
  globalThis.window = new (class extends EventTarget {})();
  const { calls } = configureDeps({
    overrides: {
      els: {
        loadMore: { addEventListener() {} },
        applyBulk: { addEventListener() {} },
        bulkAction: { value: "" },
        bulkLabelInput: { value: "", focus() {} },
        bulkToolbar: {},
        ownershipFilter: { value: "" },
        channelFilter: { value: "" },
        sortFilter: { value: "priority" },
        statusFilter: { value: "" },
        labelFilter: { value: "" },
        priorityFilter: { value: "" },
        searchInput: { value: "user-changed-search" },
      },
    },
  });
  await loadMoreConversations();
  assert.ok(!calls.includes("renderQueue:appended"));
  void calls;
});

test("applyBulkAction builds a priority payload and refreshes silently", async () => {
  globalThis.window = new (class extends EventTarget {})();
  const { calls } = configureDeps();
  await applyBulkAction({ action: "priority-high" });
  const post = calls.find((call) => call.url === "/api/conversations/bulk-actions");
  assert.deepEqual(JSON.parse(post.options.body), {
    conversation_ids: ["conv-1", "conv-2"],
    action: "set_priority",
    priority: "high",
  });
  assert.ok(calls.some((call) => call.toast === "已更新 2 个会话"));
  assert.ok(calls.some((call) => call.refreshAll && call.refreshAll.silent === true));
  assert.ok(calls.includes("renderBulkToolbar"));
  assert.equal(calls.filter((call) => call.busy !== undefined).length, 2);
});

test("applyBulkAction requires labels for label actions", async () => {
  globalThis.window = new (class extends EventTarget {})();
  const { calls } = configureDeps();
  await applyBulkAction({ action: "add-label", labels: [] });
  assert.ok(calls.some((call) => call.toast === "请输入标签" && call.isError));
  assert.equal(calls.filter((call) => call.url).length, 0);
});

test("applyBulkAction reads the legacy toolbar inputs without a source", async () => {
  globalThis.window = new (class extends EventTarget {})();
  const { els, calls } = configureDeps();
  els.bulkAction.value = "add-label";
  els.bulkLabelInput.value = "VIP, 退款";
  await applyBulkAction();
  const post = calls.find((call) => call.url === "/api/conversations/bulk-actions");
  assert.deepEqual(JSON.parse(post.options.body), {
    conversation_ids: ["conv-1", "conv-2"],
    action: "add_labels",
    labels: ["VIP", "退款"],
  });
});

test("bindQueueActions wires the controls and island bridges", () => {
  globalThis.window = new (class extends EventTarget {})();
  const { els } = configureDeps();
  assert.equal(bindQueueActions(), true);
  assert.ok(els.loadMore.listeners.click);
  assert.ok(els.applyBulk.listeners.click);
});
