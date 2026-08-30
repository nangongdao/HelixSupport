// Helix Support — composer tool-surface unit tests (D3 long tail slice 14)
// Run: node --test tests/frontend/composer-tools.test.js
//
// js/composer.js keeps the copilot/canned write cores shared by the legacy
// DOM flow and the composer island publish path; js/drafts.js owns the
// per-conversation draft persistence. These tests lock both.

import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";

import {
  COMPOSER_COPILOT_EVENT,
  applyCopilotTone,
  fetchCopilotSuggestions,
  loadCannedResponses,
  recordMacroUse,
} from "../../app/static/js/composer.js";
import {
  clearDraft,
  configure as configureDrafts,
  draftKey,
  loadDraft,
  saveDraft,
} from "../../app/static/js/drafts.js";
import { configure as configureComposer } from "../../app/static/js/composer.js";
import { escapeHtml } from "../../app/static/js/format.js";

const SUGGESTIONS = [
  { content: "建议回复一", source: "model" },
  { content: "建议回复二", source: "canned" },
];

/** Minimal window stub with an EventTarget and (optionally) localStorage. */
function installWindow({ islandMode, localStorage: storage } = {}) {
  const target = new EventTarget();
  target.__HELIX_ISLAND_MODE__ = Boolean(islandMode);
  target.setTimeout = (...args) => setTimeout(...args);
  target.clearTimeout = (...args) => clearTimeout(...args);
  if (storage) target.localStorage = storage;
  globalThis.window = target;
  return target;
}

function stubLocalStorage() {
  const store = new Map();
  return {
    getItem: (key) => (store.has(key) ? store.get(key) : null),
    setItem: (key, value) => store.set(key, String(value)),
    removeItem: (key) => store.delete(key),
    get length() {
      return store.size;
    },
    key: (index) => [...store.keys()][index] ?? null,
  };
}

function configureDeps({ overrides = {} } = {}) {
  const els = {
    operatorInput: { value: "" },
    copilotSuggestions: { hidden: true, innerHTML: "" },
    copilotKnowledge: { hidden: true, innerHTML: "" },
    copilotStatus: { hidden: true, textContent: "" },
  };
  const apiCalls = [];
  configureComposer({
    state: {
      selectedId: "conv-1",
      lastCopilotConv: null,
      cannedResponses: [],
      cannedLoadedAt: 0,
    },
    els,
    api: async (url, options) => {
      apiCalls.push({ url, options });
      if (url === "/api/canned-responses") return [{ id: "m1", title: "催单", shortcut: "cd", body: "加急中" }];
      if (url === "/api/copilot/suggest") return { suggestions: SUGGESTIONS };
      if (url === "/api/copilot/knowledge") return { articles: [{ title: "配送时效", category: "物流" }] };
      if (url === "/api/copilot/rewrite") return { rewritten: "改写后", source: "model" };
      return {};
    },
    canOperate: () => true,
    escapeHtml,
    ...overrides,
  });
  return { apiCalls, els };
}

function collectCopilot(windowStub) {
  const events = [];
  windowStub.addEventListener(COMPOSER_COPILOT_EVENT, (event) => events.push(event.detail));
  return events;
}

beforeEach(() => {
  delete globalThis.window;
});

test("island fetchCopilotSuggestions publishes suggestions instead of painting", async () => {
  const windowStub = installWindow({ islandMode: true });
  const { els } = configureDeps();
  const events = collectCopilot(windowStub);
  await fetchCopilotSuggestions({ draft: "岛屿草稿" });
  assert.equal(events.length, 2); // 生成中… then the result
  assert.equal(events[0].status, "生成中…");
  assert.deepEqual(events[1].suggestions, SUGGESTIONS);
  assert.equal(events[1].status, "");
  assert.equal(els.copilotSuggestions.innerHTML, ""); // hidden legacy tree untouched
});

test("legacy fetchCopilotSuggestions paints the chips into the copilot list", async () => {
  installWindow({ islandMode: false });
  const { els, apiCalls } = configureDeps();
  await fetchCopilotSuggestions();
  assert.match(els.copilotSuggestions.innerHTML, /copilot-suggestion-badge/);
  assert.match(els.copilotSuggestions.innerHTML, /建议回复一/);
  assert.equal(els.copilotSuggestions.hidden, false);
  assert.match(apiCalls[0].url, /\/api\/copilot\/suggest$/);
});

test("island applyCopilotTone publishes the rewritten text; legacy writes the input", async () => {
  const islandWindow = installWindow({ islandMode: true });
  const islandDeps = configureDeps();
  const events = collectCopilot(islandWindow);
  await applyCopilotTone("concise", { text: "原始草稿" });
  assert.equal(islandDeps.els.operatorInput.value, ""); // island owns its textarea
  assert.equal(events.at(-1).rewritten, "改写后");
  assert.equal(events.at(-1).status, "已改写");

  installWindow({ islandMode: false });
  const legacyDeps = configureDeps();
  await applyCopilotTone("concise", { text: "原始草稿" });
  assert.equal(legacyDeps.els.operatorInput.value, "改写后");
});

test("applyCopilotTone refuses an empty draft with the legacy status", async () => {
  const windowStub = installWindow({ islandMode: true });
  configureDeps();
  const events = collectCopilot(windowStub);
  await applyCopilotTone("concise", { text: "  " });
  assert.equal(events.at(-1).status, "先输入草稿再改写");
});

test("recordMacroUse posts the usage counter best-effort", async () => {
  installWindow({ islandMode: true });
  const { apiCalls } = configureDeps();
  await recordMacroUse("m1");
  assert.match(apiCalls[0].url, /\/api\/canned-responses\/m1\/use$/);
  assert.equal(apiCalls[0].options.method, "POST");
});

test("loadCannedResponses refreshes and republishes the composer state", async () => {
  const windowStub = installWindow({ islandMode: true });
  configureDeps();
  let published = 0;
  windowStub.HelixModules = {
    composerIslandBridge: { publishComposerState: () => { published += 1; } },
  };
  await loadCannedResponses({ force: true });
  assert.equal(published, 1);
});

test("loadCannedResponses clears the catalog for non-operators", async () => {
  const windowStub = installWindow({ islandMode: true });
  configureDeps({
    overrides: {
      state: { selectedId: "conv-1", lastCopilotConv: null, cannedResponses: [{ id: "stale" }], cannedLoadedAt: 0 },
      canOperate: () => false,
    },
  });
  let published = 0;
  windowStub.HelixModules = {
    composerIslandBridge: { publishComposerState: () => { published += 1; } },
  };
  await loadCannedResponses({ force: true });
  assert.equal(published, 1);
});

test("drafts round-trip through localStorage with TTL and disable handling", () => {
  const storage = stubLocalStorage();
  installWindow({ islandMode: false, localStorage: storage });
  configureDrafts({
    state: { me: { local_drafts_enabled: true, local_draft_ttl_minutes: 720 } },
  });
  saveDraft("conv-1", "草稿内容");
  assert.equal(loadDraft("conv-1"), "草稿内容");
  assert.equal(draftKey("conv-1"), "helix-draft:conv-1");
  clearDraft("conv-1");
  assert.equal(loadDraft("conv-1"), "");

  // Expired drafts are dropped on load.
  saveDraft("conv-2", "旧草稿");
  const raw = JSON.parse(storage.getItem(draftKey("conv-2")));
  raw.savedAt = Date.now() - 1000 * 60 * 60 * 24; // one day old, TTL 720min
  storage.setItem(draftKey("conv-2"), JSON.stringify(raw));
  assert.equal(loadDraft("conv-2"), "");

  // Disabled drafts never persist.
  configureDrafts({ state: { me: { local_drafts_enabled: false } } });
  saveDraft("conv-3", "不应保存");
  assert.equal(loadDraft("conv-3"), "");
  assert.equal(storage.getItem(draftKey("conv-3")), null);
});
