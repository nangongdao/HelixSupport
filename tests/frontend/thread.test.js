// Helix Support — thread domain module unit tests (D3 long tail glue slice)
// Run: node --test tests/frontend/thread.test.js
//
// js/thread.js keeps the transcript lifecycle (render/paginate/feedback/
// translate) shared by the legacy DOM path and the thread island publish
// path. These tests lock the snapshot builder, the shared write cores, the
// legacy renderer output, and the island publish/no-op split.

import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";

import {
  THREAD_EVENTS,
  ROLE_NAMES,
  buildTranslateResultHtml,
  configure,
  currentThreadSnapshot,
  loadOlderMessages,
  publishThreadState,
  recordFeedback,
  renderMessages,
  requestTranslation,
} from "../../app/static/js/thread.js";
import { escapeHtml, formatTime } from "../../app/static/js/format.js";

const MESSAGES = [
  { id: "msg-1", role: "customer", content: "配送一般多久能到？", created_at: "2026-08-30T08:00:00Z" },
  {
    id: "msg-2",
    role: "assistant",
    content: "根据当前服务政策 48 小时内送达 @duty.lead",
    created_at: "2026-08-30T08:00:05Z",
    metadata: { agent: "knowledge" },
  },
];

/** Minimal window stub (EventTarget) with the island-mode flag. */
function installWindow({ islandMode } = {}) {
  const target = new EventTarget();
  target.__HELIX_ISLAND_MODE__ = Boolean(islandMode);
  globalThis.window = target;
  return target;
}

function configureDeps({ overrides = {} } = {}) {
  const els = {
    messages: {
      innerHTML: "",
      scrollTop: 0,
      scrollHeight: 0,
      querySelector: () => null,
    },
  };
  const apiCalls = [];
  const deps = {
    state: {
      selectedId: "conv-1",
      detail: { messages: MESSAGES },
      threadPrevCursor: null,
      threadLoadingOlder: false,
      lowPerf: false,
    },
    els,
    api: async (url, options) => {
      apiCalls.push({ url, options });
      return {};
    },
    apiWithHeaders: async (url) => {
      apiCalls.push({ url });
      return {
        response: { headers: { get: (name) => (name === "X-Has-More" ? "false" : null) } },
        data: [{ id: "msg-0", role: "customer", content: "更早的消息", created_at: "2026-08-29T08:00:00Z" }],
      };
    },
    showToast: async () => {},
    escapeHtml,
    formatTime,
    icon: (name) => `<svg data-icon="${name}"></svg>`,
    attachmentChips: (ids) => ids.map((id) => `<span data-chip="${id}"></span>`).join(""),
    canWriteConversations: () => true,
    languageNames: { zh: "中文", en: "English" },
    languageOptions: '<option value="zh">中文</option><option value="en">English</option>',
    olderPageSize: 50,
    ...overrides,
  };
  configure(deps);
  return { apiCalls, els };
}

function collect(windowStub, eventName) {
  const events = [];
  windowStub.addEventListener(eventName, (event) => events.push(event.detail));
  return events;
}

beforeEach(() => {
  delete globalThis.window;
});

test("ROLE_NAMES covers the four transcript roles", () => {
  assert.deepEqual({ ...ROLE_NAMES }, {
    customer: "客户",
    assistant: "自动客服",
    operator: "人工客服",
    internal_note: "内部备注",
  });
});

test("currentThreadSnapshot builds sorted languages and feeds attachment meta", () => {
  installWindow({ islandMode: true });
  globalThis.window.HelixModules = {
    attachment: { attachmentMetaSnapshot: () => ({ "att-1": { filename: "发货单.pdf" } }) },
  };
  configureDeps();
  const snapshot = currentThreadSnapshot({ messages: MESSAGES });
  assert.deepEqual(snapshot.messages, MESSAGES);
  assert.deepEqual(snapshot.languages, [
    { code: "zh", name: "中文" },
    { code: "en", name: "English" },
  ]);
  assert.deepEqual(snapshot.attachmentMeta, { "att-1": { filename: "发货单.pdf" } });
  assert.equal(snapshot.canTranslate, true);
  assert.equal(snapshot.loading, false);
  const loading = currentThreadSnapshot({ loading: true });
  assert.deepEqual(loading.messages, []);
  assert.equal(loading.threadPrevCursor, null);
});

test("publishThreadState dispatches only in island mode", () => {
  const legacyWindow = installWindow({ islandMode: false });
  configureDeps();
  assert.equal(publishThreadState({ messages: MESSAGES }), false);
  void legacyWindow;
  const islandWindow = installWindow({ islandMode: true });
  configureDeps();
  const events = collect(islandWindow, THREAD_EVENTS.STATE);
  assert.equal(publishThreadState({ messages: MESSAGES }), true);
  assert.equal(events.length, 1);
  assert.deepEqual(events[0].messages, MESSAGES);
});

test("legacy renderMessages paints bubbles, chips and bars into #messages", () => {
  installWindow({ islandMode: false });
  const { els } = configureDeps();
  renderMessages(MESSAGES);
  assert.match(els.messages.innerHTML, /message-row customer/);
  assert.match(els.messages.innerHTML, /message-row assistant/);
  assert.match(els.messages.innerHTML, /agent-chip/);
  assert.match(els.messages.innerHTML, /mention-chip">@duty.lead</);
  assert.match(els.messages.innerHTML, /data-feedback="1"/);
  assert.match(els.messages.innerHTML, /translate-bar" data-message-id="msg-1"/);
  assert.equal(els.messages.scrollTop, 0); // scrollHeight 0 in the stub
});

test("legacy renderMessages renders the empty state and keeps XSS escaped", () => {
  installWindow({ islandMode: false });
  const { els } = configureDeps();
  renderMessages([]);
  assert.match(els.messages.innerHTML, /等待第一条客户消息/);
  renderMessages([
    { id: "m", role: "customer", content: "<script>alert(1)</script>", created_at: "2026-08-30T08:00:00Z" },
  ]);
  assert.doesNotMatch(els.messages.innerHTML, /<script>/);
});

test("island-mode renderMessages publishes instead of painting the hidden container", () => {
  installWindow({ islandMode: true });
  configureDeps();
  const events = collect(globalThis.window, THREAD_EVENTS.STATE);
  renderMessages(MESSAGES, { preserveAnchor: true });
  assert.equal(events.length, 1);
  assert.equal(events[0].preserveAnchor, true);
  assert.equal(events[0].messages, MESSAGES);
});

test("recordFeedback posts to the conversation feedback endpoint", async () => {
  installWindow({ islandMode: true });
  const { apiCalls } = configureDeps();
  await recordFeedback({ messageId: "msg-2", rating: "-1" });
  assert.equal(apiCalls.length, 1);
  assert.match(apiCalls[0].url, /\/api\/conversations\/conv-1\/feedback$/);
  assert.deepEqual(JSON.parse(apiCalls[0].options.body), { message_id: "msg-2", rating: -1 });
});

test("requestTranslation posts the target language for the message", async () => {
  installWindow({ islandMode: true });
  const { apiCalls } = configureDeps();
  await requestTranslation({ messageId: "msg-1", language: "en" });
  assert.match(apiCalls[0].url, /\/api\/conversations\/conv-1\/messages\/msg-1\/translate$/);
  assert.deepEqual(JSON.parse(apiCalls[0].options.body), { target_language: "en" });
});

test("buildTranslateResultHtml mirrors the legacy three outcomes with escaping", () => {
  installWindow({ islandMode: false });
  configureDeps();
  assert.equal(
    buildTranslateResultHtml({ was_translated: true, translated: "<b>hi</b>" }, "en"),
    '<span class="translate-text">&lt;b&gt;hi&lt;/b&gt;</span><span class="translate-tag">已翻译为 English</span>',
  );
  assert.equal(
    buildTranslateResultHtml({ was_translated: false, source: "none" }, "zh"),
    "目标语言与当前服务语言一致，无需翻译",
  );
  assert.equal(
    buildTranslateResultHtml({ was_translated: false, source: "rule" }, "zh"),
    "当前无翻译模型，已返回原文",
  );
});

test("loadOlderMessages merges the older page, updates the cursor and re-renders", async () => {
  installWindow({ islandMode: false });
  const { els } = configureDeps({
    overrides: {
      state: {
        selectedId: "conv-1",
        detail: { messages: MESSAGES },
        threadPrevCursor: "cursor-1",
        threadLoadingOlder: false,
        lowPerf: false,
      },
    },
  });
  await loadOlderMessages();
  assert.match(els.messages.innerHTML, /更早的消息/);
  assert.match(els.messages.innerHTML, /配送一般多久能到/);
});

test("loadOlderMessages publishes a preserveAnchor snapshot in island mode", async () => {
  installWindow({ islandMode: true });
  configureDeps({
    overrides: {
      state: {
        selectedId: "conv-1",
        detail: { messages: MESSAGES },
        threadPrevCursor: "cursor-1",
        threadLoadingOlder: false,
        lowPerf: false,
      },
      apiWithHeaders: async () => ({
        response: { headers: { get: (name) => (name === "X-Has-More" ? "true" : "cursor-0") } },
        data: [{ id: "msg-0", role: "customer", content: "更早", created_at: "2026-08-29T08:00:00Z" }],
      }),
    },
  });
  const events = collect(globalThis.window, THREAD_EVENTS.STATE);
  await loadOlderMessages();
  assert.equal(events.length, 1);
  assert.equal(events[0].preserveAnchor, true);
  assert.equal(events[0].messages.length, 3);
});
