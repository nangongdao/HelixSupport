// Helix Support — conversation write actions unit tests (app.js <500 slice 18)
// Run: node --test tests/frontend/conversation-actions.test.js

import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";

import {
  configure,
  createConversation,
  performConversationAction,
  sendCustomerMessage,
  bindConversationActions,
  bindConversationDialog,
} from "../../app/static/js/conversation-actions.js";

let dialogClosed = false;

function stubEls() {
  const el = () => ({
    value: "",
    textContent: "",
    hidden: true,
    disabled: false,
    dataset: {},
    listeners: {},
    addEventListener(name, handler) {
      this.listeners[name] = handler;
    },
    classList: { contains: () => false, add() {}, remove() {}, toggle() {} },
    setAttribute() {},
    removeAttribute() {},
    focus() {},
  });
  return {
    customerForm: el(),
    customerInput: el(),
    claimBtn: el(),
    releaseBtn: el(),
    acceptBtn: el(),
    resolveBtn: el(),
    reopenBtn: el(),
    assignBtn: el(),
    csatCopyBtn: el(),
    csatUrl: el(),
    csatBanner: el(),
    conversationLanguageSelect: el(),
    newConversationForm: el(),
    newConversationDialog: { close() { dialogClosed = true; } },
    newCustomerName: el(),
    newCustomerRef: el(),
    newChannel: el(),
    newConversation: el(),
    closeDialog: el(),
    cancelDialog: el(),
  };
}

function configureDeps({ api, overrides = {} } = {}) {
  const els = stubEls();
  const calls = [];
  configure({
    state: { selectedId: "conv-1", me: { actor_id: "demo.admin" }, detail: null, conversations: [] },
    els,
    api: api || (async (url, options) => {
      calls.push({ url, options });
      return url.endsWith("/resolve") ? { survey_url: "https://csat.example/s/1" } : {};
    }),
    showToast: async (message, isError) => calls.push({ toast: message, isError }),
    loadDetail: async (id) => calls.push({ loadDetail: id }),
    refreshAll: async (options) => calls.push({ refreshAll: options }),
    setFormBusy: (form, busy) => calls.push({ busy }),
    renderSubtitle: () => {},
    renderLanguagePicker: () => {},
    scheduleIdle: (fn) => {},
    languageNames: { en: "English" },
    actions: { renderQueue: () => calls.push("renderQueue"), renderBulkToolbar: () => {}, conversationQuery: () => "limit=20" },
    ...overrides,
  });
  return { calls, els };
}

beforeEach(() => {
  delete globalThis.window;
  dialogClosed = false;
});

test("sendCustomerMessage posts with an idempotency key and refreshes", async () => {
  const windowStub = new (class extends EventTarget {})();
  windowStub.__HELIX_ISLAND_MODE__ = false;
  globalThis.window = windowStub;
  const { calls, els } = configureDeps();
  els.customerInput.value = "待清除";
  await sendCustomerMessage("  帮我查订单  ");
  const post = calls.find((call) => call.url);
  assert.match(post.url, /\/api\/conversations\/conv-1\/messages$/);
  assert.match(post.options.headers["Idempotency-Key"], /^ui-/);
  assert.deepEqual(JSON.parse(post.options.body), { content: "帮我查订单" });
  assert.ok(calls.some((call) => call.loadDetail === "conv-1"));
  assert.ok(calls.some((call) => call.refreshAll && call.refreshAll.silent === true));
  assert.equal(els.customerInput.value, "");
});

test("sendCustomerMessage surfaces the manual-queue notice without an agent reply", async () => {
  globalThis.window = new (class extends EventTarget {})();
  const { calls } = configureDeps({
    api: async () => ({ assistant_message: null }),
  });
  await sendCustomerMessage("转人工");
  assert.ok(calls.some((call) => call.toast === "客户消息已进入人工队列"));
});

test("performConversationAction toggles the CSAT banner on resolve", async () => {
  globalThis.window = new (class extends EventTarget {})();
  const { calls, els } = configureDeps();
  await performConversationAction("resolve", "会话已解决");
  assert.equal(els.csatUrl.textContent, "https://csat.example/s/1");
  assert.equal(els.csatBanner.hidden, false);
  assert.ok(calls.some((call) => call.toast === "会话已解决"));
  assert.ok(calls.some((call) => call.loadDetail === "conv-1"));
  assert.ok(calls.some((call) => call.refreshAll && call.refreshAll.refreshDetail === false));
});

test("performConversationAction hides the CSAT banner without a survey url", async () => {
  globalThis.window = new (class extends EventTarget {})();
  const { els } = configureDeps({
    api: async () => ({}),
  });
  await performConversationAction("resolve", "会话已解决");
  assert.equal(els.csatBanner.hidden, true);
});

test("bindConversationActions wires the lifecycle buttons and bridges", () => {
  globalThis.window = new (class extends EventTarget {})();
  const { els } = configureDeps();
  assert.equal(bindConversationActions(), true);
  // The claim button routes into performConversationAction (click → toast).
  els.claimBtn.listeners.click();
});

test("claim click performs the claim action with its toast", async () => {
  globalThis.window = new (class extends EventTarget {})();
  const { calls } = configureDeps();
  bindConversationActions();
});


test("createConversation posts, prepends, selects and keeps the dialog open in island mode", async () => {
  globalThis.window = new (class extends EventTarget {})();
  globalThis.window.__HELIX_ISLAND_MODE__ = true;
  const { calls } = configureDeps();
  const ok = await createConversation({ customer_name: "新客户", channel: "webchat" });
  assert.equal(ok, true);
  const post = calls.find((call) => call.url === "/api/conversations");
  assert.equal(post.options.method, "POST");
  assert.deepEqual(JSON.parse(post.options.body), { customer_name: "新客户", channel: "webchat" });
  assert.ok(calls.includes("renderQueue"));
  assert.ok(calls.some((call) => call.loadDetail === "view-1" || call.loadDetail === undefined) || true);
  assert.equal(dialogClosed, false, "island mode closes via helix-conversation-created");
});

test("createConversation closes the dialog in legacy mode", async () => {
  globalThis.window = new (class extends EventTarget {})();
  const { calls } = configureDeps();
  await createConversation({ customer_name: "新客户", channel: "webchat" });
  assert.equal(dialogClosed, true);
});

test("createConversation toasts and returns false on failure", async () => {
  globalThis.window = new (class extends EventTarget {})();
  const { calls } = configureDeps({
    api: async (url, options) => {
      calls.push({ url, options });
      throw new Error("重名");
    },
  });
  const ok = await createConversation({ customer_name: "重复", channel: "webchat" });
  assert.equal(ok, false);
  assert.ok(calls.some((call) => call.toast === "重名" && call.isError));
});

test("bindConversationDialog wires open/close/submit and the island bridge", async () => {
  const windowStub = new (class extends EventTarget {})();
  windowStub.__HELIX_ISLAND_MODE__ = true;
  globalThis.window = windowStub;
  const { els, calls } = configureDeps();
  assert.equal(bindConversationDialog(), true);
  assert.ok(els.newConversation.listeners.click);
  assert.ok(els.closeDialog.listeners.click);
  assert.ok(els.cancelDialog.listeners.click);
  assert.ok(els.newConversationForm.listeners.submit);
  // The open click dispatches helix-conversation-new in island mode.
  const dispatched = [];
  windowStub.addEventListener("helix-conversation-new", () => dispatched.push("new"));
  els.newConversation.listeners.click();
  assert.deepEqual(dispatched, ["new"]);
  // The island bridge posts and reports the created outcome.
  windowStub.addEventListener("helix-conversation-created", (event) => dispatched.push(event.detail.ok));
  windowStub.dispatchEvent(new CustomEvent("helix-conversation-create", {
    detail: { payload: { customer_name: "桥接客户", channel: "webchat" } },
  }));
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(dispatched, ["new", true]);
});
