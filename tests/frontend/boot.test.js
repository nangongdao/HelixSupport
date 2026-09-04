// Helix Support — legacy boot assembly unit tests (app.js <500 campaign)
// Run: node --test tests/frontend/boot.test.js

import { afterEach, beforeEach, test } from "node:test";
import assert from "node:assert/strict";

import { bindLegacyBoot, configure } from "../../app/static/js/boot.js";

function makeEl(extra = {}) {
  const self = {
    listeners: {},
    value: "",
    hidden: false,
    dataset: {},
    classList: { contains: () => false, add() {}, remove() {}, toggle() {} },
    addEventListener(type, handler) {
      self.listeners[type] = handler;
    },
    fire(type, event = {}) {
      const h = self.listeners[type];
      if (h) h({ target: self, ...event });
    },
    closest(sel) {
      return null;
    },
    ...extra,
  };
  return self;
}

function installWindow(extra = {}) {
  const target = { ...extra };
  const listeners = {};
  target.addEventListener = (type, handler) => {
    (listeners[type] = listeners[type] || []).push(handler);
  };
  target.dispatchEvent = () => true;
  target.localStorage = {
    _s: {},
    getItem: (k) => (k in target.localStorage._s ? target.localStorage._s[k] : null),
    setItem: (k, v) => { target.localStorage._s[k] = String(v); },
  };
  target.HelixModules = {};
  target._listeners = listeners;
  target.fire = (type, detail) => (listeners[type] || []).forEach((h) => h({ detail }));
  return target;
}

function configureDeps(overrides = {}) {
  const calls = [];
  const els = {
    list: makeEl(),
    bulkAction: makeEl(),
    clearBulk: makeEl(),
    densityToggle: makeEl(),
    lowPerfToggle: makeEl(),
    inspectorToggle: makeEl(),
    macroSuggest: makeEl(),
    cannedList: makeEl(),
    appNav: makeEl(),
    quotaForm: makeEl(),
    memberForm: makeEl(),
    webhookForm: makeEl(),
    refreshAdmin: makeEl(),
    memberList: makeEl(),
    webhookList: makeEl(),
  };
  const win = installWindow({ __HELIX_ISLAND_MODE__: false });
  const document = {
    querySelectorAll: () => [],
  };
  const state = {
    selectedId: null,
    detail: null,
    lowPerf: false,
    inspectorCollapsed: false,
    density: "comfortable",
    bulkSelected: new Set(),
  };
  const ctx = {
    state,
    els,
    document,
    window: win,
    setDensity: () => calls.push("setDensity"),
    nextDensity: (l) => l,
    renderQueue: () => calls.push("renderQueue"),
    renderBulkToolbar: () => calls.push("renderBulkToolbar"),
    renderMetrics: () => calls.push("renderMetrics"),
    renderLoadingQueue: () => calls.push("renderLoadingQueue"),
    detectConstrainedDevice: () => false,
    normalizeDensity: (l) => l || "comfortable",
    applyWorkspacePreferences: () => calls.push("applyWorkspacePreferences"),
    schedulePolling: () => calls.push("schedulePolling"),
    showToast: () => calls.push("showToast"),
    refreshAll: () => calls.push("refreshAll"),
    loadSavedViews: () => calls.push("loadSavedViews"),
    handleQueueScroll: () => {},
    switchInspectorTab: () => {},
    renderInspector: () => {},
    applyMacroFromSuggest: () => {},
    insertCannedResponse: () => {},
    switchAppView: () => {},
    renderSummaries: () => {},
    selectConversation: (id) => calls.push({ select: id }),
    renderWebhookEventCheckboxes: () => {},
    saveQuota: () => {}, inviteMember: () => {}, registerWebhook: () => {},
    loadAdminView: () => {},
    saveQuotaFromIsland: () => {}, inviteMemberFromIsland: () => {},
    changeMemberRoleFromIsland: () => {}, deactivateMemberFromIsland: () => {},
    registerWebhookFromIsland: () => {}, deleteWebhookFromIsland: () => {},
    createSubscriptionFromIsland: () => {}, toggleSubscriptionFromIsland: () => {},
    deleteSubscriptionFromIsland: () => {}, generateReportFromIsland: () => {},
    saveSlaFromIsland: () => {}, createRuleFromIsland: () => {},
    deleteRuleFromIsland: () => {},
    changeMemberRole: () => {}, deactivateMember: () => {}, deleteWebhook: () => {},
    PREF_LOW_PERF: "helix-low-perf",
    PREF_INSPECTOR: "helix-inspector-collapsed",
    PREF_DENSITY: "helix-queue-density",
    ...overrides,
  };
  configure(ctx);
  return { ctx, calls, els, win, state };
}

let originalWindow;
beforeEach(() => {
  originalWindow = globalThis.window;
});
afterEach(() => {
  configure(null);
  if (originalWindow === undefined) delete globalThis.window;
  else globalThis.window = originalWindow;
});

test("boot wires the queue row click to selectConversation", () => {
  const { els, calls } = configureDeps();
  bindLegacyBoot();
  els.list.fire("click", {
    target: { closest: (sel) => (sel === ".conversation-item" ? { dataset: { id: "c1" } } : null) },
  });
  assert.deepEqual(calls.find((c) => c.select), { select: "c1" });
});

test("boot wires the density toggle to a re-measure (non-low-perf)", () => {
  const { els, calls } = configureDeps();
  bindLegacyBoot();
  els.densityToggle.fire("click");
  assert.ok(calls.includes("setDensity"));
  assert.ok(calls.includes("renderQueue"));
});

test("boot hydrates preferences and runs the initial paint/refresh", () => {
  const { calls } = configureDeps();
  bindLegacyBoot();
  for (const expected of ["renderMetrics", "renderLoadingQueue", "applyWorkspacePreferences", "schedulePolling", "loadSavedViews", "refreshAll"]) {
    assert.ok(calls.includes(expected), `expected ${expected}`);
  }
  // low-perf detected false -> density stays comfortable, no persist eval.
  assert.ok(calls.includes("setDensity"));
});

test("boot registers the admin island write bridges on window", () => {
  const { win, ctx, calls } = configureDeps();
  bindLegacyBoot();
  assert.ok(win._listeners["helix-admin-save-quota"], "save-quota bridge registered");
  assert.ok(win._listeners["helix-admin-invite-member"], "invite-member bridge registered");
  assert.ok(win._listeners["helix-admin-generate-report"], "generate-report bridge registered");
  // Firing a bridge calls its handler with the payload.
  const saveQuotaSpy = (payload) => calls.push({ saveQuotaIsland: payload });
  ctx.saveQuotaFromIsland = saveQuotaSpy;
  bindLegacyBoot(); // re-register so the spy is captured
  win._listeners["helix-admin-save-quota"].forEach((h) => h({ detail: { quota: 100 } }));
  assert.ok(calls.some((c) => c.saveQuotaIsland && c.saveQuotaIsland.quota === 100));
});

test("boot can run with undefined optional elements (appNav/forms may be absent)", () => {
  const { els } = configureDeps();
  delete els.appNav;
  delete els.quotaForm;
  bindLegacyBoot(); // must not throw when optional elements are absent
});