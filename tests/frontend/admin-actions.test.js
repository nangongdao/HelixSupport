// Helix Support — admin quota/member/webhook CRUD tests (app.js <500 slice 25)
// Run: node --test tests/frontend/admin-actions.test.js

import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";

import {
  adminTenantId,
  configure,
  inviteMemberFromIsland,
  loadAdminView,
  renderMembers,
  renderWebhookEventCheckboxes,
  saveQuotaFromIsland,
} from "../../app/static/js/admin-actions.js";

function stubEls() {
  const el = () => ({
    value: "",
    textContent: "",
    innerHTML: "",
    hidden: true,
    disabled: false,
    attrs: {},
    setAttribute(name, value) { this.attrs[name] = value; },
    removeAttribute(name) { delete this.attrs[name]; },
    querySelectorAll: () => [],
    addEventListener() {},
  });
  return {
    adminView: el(),
    adminDenied: el(),
    adminContent: el(),
    webhookEvents: el(),
    quotaReadout: el(),
    memberList: el(),
    webhookList: el(),
    quotaConversations: el(),
    quotaStorageMb: el(),
  };
}

function configureDeps({ me, apiImpl, isAdmin = true } = {}) {
  const els = stubEls();
  const calls = { api: [], toasts: [], saved: [], actions: [] };
  const savedEvents = [];
  const windowStub = new (class extends EventTarget {})();
  windowStub.confirm = () => true;
  windowStub.addEventListener("helix-admin-saved", (event) => savedEvents.push(event.detail));
  // Respect an already-installed window stub (tests may pre-configure the
  // island-mode flag and listeners before calling configureDeps).
  if (!globalThis.window) {
    windowStub.__HELIX_ISLAND_MODE__ = false;
    globalThis.window = windowStub;
  } else {
    if (globalThis.window.__HELIX_ISLAND_MODE__ === undefined) globalThis.window.__HELIX_ISLAND_MODE__ = false;
    globalThis.window.confirm = windowStub.confirm;
    globalThis.window.addEventListener("helix-admin-saved", (event) => savedEvents.push(event.detail));
  }
  configure({
    state: {
      me: me || { actor_id: "demo.admin", role: "admin", tenant_id: "demo", permissions: isAdmin ? ["admin:manage"] : [] },
      detail: null,
    },
    els,
    api: apiImpl || (async (url, options) => {
      calls.api.push({ url, options });
      if (url.endsWith("/quota")) return { tenant_id: "demo", conversation_quota: 100, storage_quota_bytes: 1048576, allowed_models: [] };
      if (url.endsWith("/members")) return [{ actor_id: "demo.admin", role: "admin", status: "active" }];
      if (url === "/api/webhooks") return [];
      return {};
    }),
    showToast: async (message, isError) => calls.toasts.push({ message, isError }),
    escapeHtml: (v) => String(v ?? ""),
    TENANT: "demo",
    roleLabels: { admin: "管理员", operator: "坐席" },
    canManage: () => isAdmin,
    actions: {
      renderReportWebhookOptions: () => calls.actions.push("renderReportWebhookOptions"),
      loadReportSubscriptions: async () => calls.actions.push("loadReportSubscriptions"),
      loadRuleGroups: async () => calls.actions.push("loadRuleGroups"),
      loadSlaPolicies: async () => calls.actions.push("loadSlaPolicies"),
      loadRoutingRules: async () => calls.actions.push("loadRoutingRules"),
      loadCsatSummary: async () => calls.actions.push("loadCsatSummary"),
      loadAdminView: async () => calls.actions.push("loadAdminView"),
    },
  });
  return { calls, els, savedEvents, windowStub };
}

beforeEach(() => {
  delete globalThis.window;
});

test("adminTenantId prefers the operator tenant and falls back to the demo tenant", () => {
  globalThis.window = new (class extends EventTarget {})();
  configureDeps();
  assert.equal(adminTenantId(), "demo");
});

test("renderWebhookEventCheckboxes renders one checkbox per contract event", () => {
  globalThis.window = new (class extends EventTarget {})();
  const { els } = configureDeps();
  renderWebhookEventCheckboxes();
  assert.match(els.webhookEvents.innerHTML, /value="conversation\.created"/);
  assert.match(els.webhookEvents.innerHTML, /会话创建/);
});

test("renderMembers excludes nobody but disables self-deactivation for the current admin", () => {
  globalThis.window = new (class extends EventTarget {})();
  const { els } = configureDeps();
  renderMembers([
    { actor_id: "demo.admin", role: "admin", status: "active" },
    { actor_id: "colleague.a", role: "operator", status: "active" },
  ]);
  assert.match(els.memberList.innerHTML, /（你）/);
  assert.match(els.memberList.innerHTML, /data-actor="colleague\.a"/);
  // The current admin's role select carries the self-guard disabled state.
  assert.match(els.memberList.innerHTML, /aria-label="变更角色" disabled/);
  // The colleague's select stays enabled.
  assert.match(els.memberList.innerHTML, /data-actor="colleague\.a" aria-label="变更角色">/);
});

test("island-mode loadAdminView hands the refresh over without fetching", async () => {
  const islandEvents = [];
  const windowStub = new (class extends EventTarget {})();
  windowStub.__HELIX_ISLAND_MODE__ = true;
  windowStub.addEventListener("helix-admin-refresh", (event) => islandEvents.push(event.detail));
  globalThis.window = windowStub;
  const { calls } = configureDeps();
  await loadAdminView();
  assert.deepEqual(islandEvents, [{ force: false }]);
  assert.equal(calls.api.length, 0, "no fetch in island mode");
});

test("saveQuotaFromIsland PUTs the quota and reports helix-admin-saved", async () => {
  const { calls, savedEvents } = configureDeps();
  await saveQuotaFromIsland({ conversation_quota: "2500", storage_quota_bytes: "134217728" });
  const put = calls.api.at(-1);
  assert.match(put.url, /\/api\/admin\/tenants\/demo\/quota$/);
  assert.equal(put.options.method, "PUT");
  assert.deepEqual(JSON.parse(put.options.body), {
    conversation_quota: 2500,
    storage_quota_bytes: 134217728,
  });
  assert.deepEqual(savedEvents, [{ ok: true, domains: ["quota"] }]);
});

test("inviteMemberFromIsland posts the member and reports the members domain", async () => {
  const { calls, savedEvents } = configureDeps();
  await inviteMemberFromIsland({ actorId: "colleague.b", role: "operator" });
  const post = calls.api.at(-1);
  assert.match(post.url, /\/api\/admin\/tenants\/demo\/members$/);
  assert.deepEqual(JSON.parse(post.options.body), { actor_id: "colleague.b", role: "operator" });
  assert.deepEqual(savedEvents, [{ ok: true, domains: ["members"] }]);
});

test("inviteMemberFromIsland with no actorId reports nothing", async () => {
  const { savedEvents } = configureDeps();
  await inviteMemberFromIsland({});
  assert.deepEqual(savedEvents, []);
});
