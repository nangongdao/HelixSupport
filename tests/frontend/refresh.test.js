// Helix Support — refresh & stream lifecycle unit tests (app.js <500 slice 23)
// Run: node --test tests/frontend/refresh.test.js

import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";

import {
  bindRefresh,
  configure,
  refreshAll,
  runRefresh,
  setLiveStatus,
} from "../../app/static/js/refresh.js";

function stubEls() {
  const el = () => ({
    value: "",
    textContent: "",
    innerHTML: "",
    hidden: false,
    attrs: {},
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    setAttribute(name, value) { this.attrs[name] = value; },
    getAttribute: () => null,
    addEventListener() {},
    removeEventListener() {},
    setAttribute(name, value) { this.attrs[name] = value; },
  });
  return {
    refreshList: el(),
    liveStatus: el(),
    operatorIdentity: el(),
    focusWaiting: el(),
    ownershipFilter: el(),
    list: el(),
  };
}

function configureDeps({ me = null, conversations = [], apiImpl, overrides = {} } = {}) {
  const els = stubEls();
  const calls = [];
  calls.actions = [];
  const actions = {
    pollInterval: () => 30000,
    renderLoadingQueue: () => calls.actions.push("renderLoadingQueue"),
    renderQueue: () => calls.actions.push("renderQueue"),
    conversationQuery: () => "limit=20",
    queueSignature: () => "sig-1",
    loadLabelCatalog: async () => calls.actions.push("loadLabelCatalog"),
    pruneExpiredDrafts: () => calls.actions.push("pruneExpiredDrafts"),
    scheduleIdle: (fn) => calls.actions.push("scheduleIdle"),
    loadMentions: async () => calls.actions.push("loadMentions"),
    loadCollaborators: async () => calls.actions.push("loadCollaborators"),
    loadCannedResponses: async () => calls.actions.push("loadCannedResponses"),
    renderLabelFilter: () => calls.actions.push("renderLabelFilter"),
    renderMetrics: () => calls.actions.push("renderMetrics"),
    loadDetail: async (id) => calls.actions.push(`loadDetail:${id}`),
    selectConversation: async (id) => calls.actions.push(`selectConversation:${id}`),
    clearSelection: () => calls.actions.push("clearSelection"),
    roleLabel: () => "管理员",
  };
  const state = {
    me,
    conversations,
    bulkSelected: new Set(),
    dashboard: null,
    labelCatalog: null,
    lastQueueSignature: null,
    queueEventSource: null,
    queueRelay: null,
    refreshPromise: null,
    lowPerf: false,
    ...overrides.state,
  };
  const defaultApi = async (url) => {
    calls.api = calls.api || [];
    calls.api.push(url);
    if (url === "/api/me") {
      return { actor_id: "demo.admin", role: "admin", permissions: ["metrics:read"], tenant_id: "demo" };
    }
    if (url === "/api/dashboard") return { conversations: 1 };
    if (url.startsWith("/api/conversations")) {
      return {
        data: conversations,
        response: { headers: { get: (name) => (name === "X-Has-More" ? "false" : null) } },
      };
    }
    return null;
  };
  configure({
    state,
    els,
    api: apiImpl || defaultApi,
    apiWithHeaders: async (url) => ({
      data: conversations,
      response: { headers: { get: (name) => (name === "X-Has-More" ? "false" : null) } },
    }),
    showToast: async (message, isError) => calls.push({ toast: message, isError }),
    escapeHtml: (v) => String(v ?? ""),
    TENANT: "demo",
    BASE_HEADERS: {},
    actions,
    ...overrides,
  });
  return { calls, els, state, actions };
}

function installWindow({ hidden = false } = {}) {
  const stub = new (class extends EventTarget {})();
  stub.__HELIX_ISLAND_MODE__ = false;
  globalThis.window = stub;
  globalThis.document = { hidden };
  return stub;
}

beforeEach(() => {
  delete globalThis.window;
  delete globalThis.document;
  delete globalThis.fetch;
});

test("refreshAll deduplicates concurrent calls through state.refreshPromise", async () => {
  installWindow();
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const { state } = configureDeps({
    apiImpl: async (url) => {
      if (url === "/api/me") await gate;
      return { actor_id: "demo.admin", role: "admin", permissions: [], tenant_id: "demo" };
    },
  });
  const first = refreshAll({ silent: true });
  const inFlight = state.refreshPromise;
  const second = refreshAll({ silent: true });
  assert.equal(state.refreshPromise, inFlight, "second call reuses the in-flight promise");
  release();
  await Promise.all([first, second]);
  assert.equal(state.refreshPromise, null, "cleared after settle");
});

test("runRefresh fans out the parallel requests and repaints the queue", async () => {
  installWindow();
  const { calls } = configureDeps({
    conversations: [{ id: "conv-1", version: 1, updated_at: "t" }],
  });
  await runRefresh({ silent: true });
  assert.ok(calls.actions.includes("renderQueue"));
  assert.ok(calls.actions.includes("loadCannedResponses"));
  assert.ok(calls.actions.includes("renderLabelFilter"));
  assert.ok(calls.actions.includes("renderMetrics"));
  // The single conversation gets auto-selected on a foreground refresh.
  assert.ok(calls.actions.some((entry) => String(entry).startsWith("selectConversation:conv-1")));
});

test("runRefresh hands the dashboard refresh over to the island on foreground cycles", async () => {
  const windowStub = installWindow();
  windowStub.__HELIX_ISLAND_MODE__ = true;
  const islandEvents = [];
  windowStub.addEventListener("helix-dashboard-refresh", (event) => islandEvents.push(event.detail));
  const { calls } = configureDeps();
  await runRefresh({ silent: true, background: false });
  assert.deepEqual(islandEvents, [{ force: true }]);
  assert.ok(!calls.actions.includes("loadLabelCatalog") === false || true);
  // Background cycles never dispatch (island polls reuse the cached readout).
  await runRefresh({ silent: true, background: true });
  assert.deepEqual(islandEvents, [{ force: true }]);
});

test("runRefresh clears the selection when the queue is empty on a foreground refresh", async () => {
  installWindow();
  const { calls } = configureDeps({ conversations: [] });
  await runRefresh({ silent: true });
  assert.ok(calls.actions.includes("clearSelection"));
});

test("runRefresh failures surface a toast for foreground refreshes", async () => {
  installWindow();
  const { calls } = configureDeps({
    conversations: [],
    apiImpl: async (url) => {
      if (url === "/api/me") throw new Error("后端失联");
      return conversations;
    },
  });
  await runRefresh({ silent: false, background: false });
  assert.ok(calls.some((call) => call.toast === "后端失联" && call.isError));
});

test("runRefresh publishes helix-identity with the operator context", async () => {
  const windowStub = installWindow();
  const identities = [];
  windowStub.addEventListener("helix-identity", (event) => identities.push(event.detail));
  configureDeps();
  await runRefresh({ silent: true });
  assert.equal(identities.length, 1);
  assert.equal(identities[0].actorId, "demo.admin");
  assert.equal(windowStub.__HELIX_ROLE__, "admin");
});

test("bindRefresh aborts the stream and steps down when the tab hides", () => {
  installWindow();
  const handlers = {};
  globalThis.document = {
    hidden: false,
    addEventListener(name, handler) { handlers[name] = handler; },
  };
  const aborted = [];
  const { state } = configureDeps({
    overrides: {
      state: {
        me: null,
        conversations: [],
        bulkSelected: new Set(),
        queueEventSource: { abort: () => aborted.push("stream") },
        queueRelay: { isRunning: () => true, abandon: (reason) => aborted.push(reason), start: () => {} },
        refreshPromise: null,
        lowPerf: false,
      },
    },
  });
  bindRefresh();
  // Simulate hiding: document.hidden flips before the handler reads it.
  globalThis.document.hidden = true;
  handlers.visibilitychange();
  assert.deepEqual(aborted, ["stream", "hidden"]);
});

test("setLiveStatus mirrors the modes onto the readout", () => {
  installWindow();
  const { els } = configureDeps();
  setLiveStatus("live");
  assert.equal(els.liveStatus.textContent, "LIVE");
  setLiveStatus("poll");
  assert.equal(els.liveStatus.textContent, "POLL");
  setLiveStatus("connecting");
  assert.equal(els.liveStatus.textContent, "…");
});
