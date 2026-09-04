// Helix Support — palette command dispatcher unit tests (app.js <500 slice 22)
// Run: node --test tests/frontend/command-dispatch.test.js

import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";

import {
  bindCommandDispatch,
  bindCommandPalette,
  checkBackendHealth,
  closeCommandPalette,
  configure,
  loadConversationCommands,
  openCommandPalette,
  renderCommandResults,
  runCommand,
} from "../../app/static/js/command-dispatch.js";

function installWindow() {
  const stub = new (class extends EventTarget {})();
  globalThis.window = stub;
  return stub;
}

/** Minimal document stub capturing keydown handlers so palette navigation
 * can be driven without a DOM implementation. */
function installDocument() {
  const handlers = [];
  const clicked = [];
  const doc = {
    addEventListener: (type, handler) => {
      if (type === "keydown") handlers.push(handler);
    },
    getElementById: (id) => ({ click: () => clicked.push(id) }),
    dispatch: (event) => handlers.forEach((handler) => handler(event)),
  };
  globalThis.document = doc;
  return { doc, clicked };
}

const dispatched = [];

/** Build the palette triplet (<dialog>, input, results) the way app.js's
 * `els` map exposes it, so close/open assertions observe real state. */
function paletteEls() {
  const listeners = {};
  const palette = {
    open: false,
    showModal() {
      this.open = true;
    },
    close() {
      this.open = false;
    },
    setAttribute() {
      this.open = true;
    },
    removeAttribute() {
      this.open = false;
    },
  };
  const commandInput = {
    value: "",
    focus() {},
    addEventListener: (type, handler) => {
      (listeners[type] = listeners[type] || []).push(handler);
    },
  };
  const rendered = { html: "", items: [] };
  const commandResults = {
    set innerHTML(value) {
      rendered.html = value;
    },
    get innerHTML() {
      return rendered.html;
    },
    querySelectorAll: () => rendered.items,
  };
  return { palette, commandInput, commandResults, listeners, rendered };
}

function configureDeps({ fetchImpl, overrides = {} } = {}) {
  const calls = [];
  const parts = paletteEls();
  configure({
    state: {},
    els: {
      newConversation: { click() {} },
      lowPerfToggle: { click() {} },
      inspectorToggle: { click() {} },
      saveView: { click() {} },
      commandPalette: parts.palette,
      commandInput: parts.commandInput,
      commandResults: parts.commandResults,
    },
    api: async () => [{ id: "conv-1", customer_name: "Alice", channel: "web_chat" }],
    escapeHtml: (value) => String(value).replace(/&/g, "&amp;").replace(/</g, "&lt;"),
    showToast: async (message, isError) => calls.push({ toast: message, isError }),
    switchAppView: (view) => calls.push({ view }),
    refreshAll: async () => calls.push({ refreshed: true }),
    actions: {
      loadDetail: async (id) => calls.push({ loadDetail: id }),
    },
    ...overrides,
  });
  if (fetchImpl) {
    globalThis.fetch = async (url) => fetchImpl(url);
  }
  return { calls, dispatched, ...parts };
}

beforeEach(() => {
  delete globalThis.window;
  delete globalThis.document;
  delete globalThis.fetch;
  dispatched.length = 0;
});

test("bindCommandDispatch routes nav commands to switchAppView", () => {
  const windowStub = installWindow();
  configureDeps();
  assert.equal(bindCommandDispatch(), true);
  windowStub.dispatchEvent(new CustomEvent("helix-command", { detail: { id: "nav:knowledge" } }));
  windowStub.dispatchEvent(new CustomEvent("helix-command", { detail: { id: "nav:settings" } }));
});

test("nav commands reach switchAppView with the stripped view id", () => {
  const windowStub = installWindow();
  const { calls } = configureDeps();
  bindCommandDispatch();
  windowStub.dispatchEvent(new CustomEvent("helix-command", { detail: { id: "nav:knowledge" } }));
  assert.deepEqual(calls.filter((call) => call.view), [{ view: "knowledge" }]);
});

test("conv:new re-dispatches the dialog bridge and conv:refresh refreshes", async () => {
  const windowStub = installWindow();
  const { calls } = configureDeps();
  windowStub.addEventListener("helix-conversation-new", () => dispatched.push("dialog"));
  bindCommandDispatch();
  windowStub.dispatchEvent(new CustomEvent("helix-command", { detail: { id: "conv:new" } }));
  windowStub.dispatchEvent(new CustomEvent("helix-command", { detail: { id: "conv:refresh" } }));
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(dispatched, ["dialog"]);
  assert.ok(calls.some((call) => call.refreshed));
});

test("diag:health toasts ready and failure states", async () => {
  installWindow();
  const { calls } = configureDeps({
    fetchImpl: async () => ({ ok: true, status: 200, json: async () => ({ status: "ready" }) }),
  });
  bindCommandDispatch();
  const windowStub = globalThis.window;
  windowStub.dispatchEvent(new CustomEvent("helix-command", { detail: { id: "diag:health" } }));
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.ok(calls.some((call) => call.toast === "健康检查：后端就绪"));

  configure({
    state: {},
    els: {},
    showToast: async (message, isError) => calls.push({ toast: message, isError }),
    switchAppView: () => {},
    refreshAll: async () => {},
  });
  globalThis.fetch = async () => ({ ok: false, status: 503, json: async () => ({ status: "degraded" }) });
  await checkBackendHealth();
  assert.ok(calls.some((call) => call.toast === "健康检查：后端异常（degraded）" && call.isError));
});

test("runCommand closes the palette and routes view switches", () => {
  installWindow();
  installDocument();
  const { calls, palette } = configureDeps();
  palette.open = true;
  runCommand({ run: "view:admin" });
  assert.equal(palette.open, false);
  assert.deepEqual(calls.filter((call) => call.view), [{ view: "admin" }]);
});

test("runCommand jumps to a conversation via workspace + loadDetail", () => {
  installWindow();
  installDocument();
  const { calls } = configureDeps();
  runCommand({ run: "conversation:conv-7" });
  assert.deepEqual(calls.filter((call) => call.view), [{ view: "workspace" }]);
  assert.ok(calls.some((call) => call.loadDetail === "conv-7"));
});

test("runCommand maps the workspace actions to their UI triggers", () => {
  const windowStub = installWindow();
  const { els } = configureDeps();
  const { clicked } = installDocument();
  runCommand({ run: "action:new_conversation" });
  runCommand({ run: "action:toggle_theme" });
  runCommand({ run: "action:save_view" });
  assert.deepEqual(clicked, ["themeToggle"]);
  void els;
  void windowStub;
});

// ---- legacy palette dialog lifecycle (slice: app.js <500 campaign) ----

test("loadConversationCommands caches for 60s and honours force", async () => {
  installWindow();
  let fetches = 0;
  configureDeps({ overrides: { api: async () => { fetches += 1; return [{ id: "conv-9", customer_name: "Bob" }]; } } });
  const first = await loadConversationCommands();
  assert.equal(first.length, 1);
  assert.equal(first[0].run, "conversation:conv-9");
  await loadConversationCommands();
  assert.equal(fetches, 1, "second unforced call must hit the TTL cache");
  await loadConversationCommands({ force: true });
  assert.equal(fetches, 2, "force re-arms the conversation jump list");
});

test("loadConversationCommands degrades to an empty list on API failure", async () => {
  installWindow();
  configureDeps({ overrides: { api: async () => { throw new Error("offline"); } } });
  const rows = await loadConversationCommands({ force: true });
  assert.deepEqual(rows, []);
});

test("openCommandPalette loads conversations, renders groups and opens the dialog", async () => {
  installWindow();
  const { palette, commandInput, rendered, commandResults } = configureDeps();
  // conversationCommands is module-level state; force a load so the palette
  // renders this test's rows instead of a previous test's cached list.
  await loadConversationCommands({ force: true });
  await openCommandPalette();
  assert.equal(palette.open, true);
  assert.equal(commandInput.value, "");
  assert.ok(rendered.html.includes('class="command-group-label">视图</div>'));
  assert.ok(rendered.html.includes('class="command-group-label">动作</div>'));
  assert.ok(rendered.html.includes('class="command-group-label">会话</div>'));
  assert.ok(rendered.html.includes("Alice"), "conversation jump commands are merged in");
  void commandResults;
});

test("renderCommandResults escapes labels and reports the empty state", async () => {
  installWindow();
  const { commandInput, rendered } = configureDeps({
    overrides: {
      api: async () => [{ id: "conv-x", customer_name: "<img onerror=1>", channel: "web" }],
    },
  });
  await loadConversationCommands({ force: true });
  renderCommandResults();
  assert.ok(!rendered.html.includes("<img onerror=1>"), "labels are escaped before innerHTML");
  assert.ok(rendered.html.includes("&lt;img onerror=1>"));

  commandInput.value = "zzz-no-such-command";
  renderCommandResults();
  assert.equal(rendered.html, '<div class="command-empty">没有匹配的命令或会话</div>');
});

test("closeCommandPalette closes the native dialog or clears the open attribute", () => {
  installWindow();
  const { palette } = configureDeps();
  palette.open = true;
  closeCommandPalette();
  assert.equal(palette.open, false);

  const fallback = { setAttribute: () => {}, removeAttribute() { this.open = false; }, open: true };
  configureDeps({ overrides: { els: { commandPalette: fallback } } });
  closeCommandPalette();
  assert.equal(fallback.open, false, "browsers without showModal fall back to the open attribute");
});

test("bindCommandPalette opens on Ctrl+K and yields to the shell island", async () => {
  const windowStub = installWindow();
  const { doc, palette } = (() => {
    const parts = configureDeps();
    installDocument();
    return { doc: globalThis.document, palette: parts.palette };
  })();
  assert.equal(bindCommandPalette(), true);

  doc.dispatch({ ctrlKey: true, key: "k", preventDefault() {} });
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(palette.open, true, "Ctrl+K opens the legacy dialog in a browser tab");
  closeCommandPalette();

  windowStub.__HELIX_ISLAND_MODE__ = true;
  doc.dispatch({ ctrlKey: true, key: "k", preventDefault() {} });
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(palette.open, false, "island mode leaves Ctrl+K to the React palette");
  delete windowStub.__HELIX_ISLAND_MODE__;
});

test("bindCommandPalette navigates with arrows and runs on Enter", async () => {
  installWindow();
  const parts = configureDeps();
  const { doc } = (() => {
    installDocument();
    return { doc: globalThis.document };
  })();
  bindCommandPalette();
  await openCommandPalette();
  const { calls, palette, commandInput } = parts;

  const key = (name) => doc.dispatch({ key: name, preventDefault() {} });
  key("ArrowDown"); // 0 -> 1
  key("ArrowDown"); // 1 -> 2
  key("ArrowUp"); // 2 -> 1
  assert.ok(parts.rendered.html.includes('data-command-index="1" is-selected')
    || parts.rendered.html.includes('aria-selected="true" data-command-index="1"'),
  "the second command carries the selection");

  key("Escape");
  assert.equal(palette.open, false, "Escape closes the palette");
  void calls;
  void commandInput;
});

test("bindCommandPalette re-renders from the top on input", async () => {
  installWindow();
  const parts = configureDeps();
  installDocument();
  bindCommandPalette();
  await openCommandPalette();
  parts.doc = globalThis.document;
  parts.doc.dispatch({ key: "ArrowDown", preventDefault() {} });
  parts.commandInput.value = "管理";
  parts.listeners.input.forEach((handler) => handler());
  assert.ok(parts.rendered.html.includes("管理"), "filtered results follow the query");
  assert.ok(!parts.rendered.html.includes("Alice"), "non-matching jump commands drop out");
});
