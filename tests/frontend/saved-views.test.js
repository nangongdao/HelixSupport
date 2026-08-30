// Helix Support — saved-views lifecycle unit tests (app.js <500 slice 21 + 23)
// Run: node --test tests/frontend/saved-views.test.js

import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";

import {
  applySavedView,
  bindSavedViews,
  configure,
  currentViewFilters,
  deleteSavedView,
  loadSavedViews,
  renderSavedViews,
  saveSavedView,
} from "../../app/static/js/saved-views.js";

/** Minimal element stub: inputs carry `value`, controls carry listeners and
 * (for the select) an innerHTML surface. */
function el(extra = {}) {
  return {
    value: "",
    disabled: true,
    innerHTML: "",
    attributes: {},
    listeners: {},
    addEventListener(name, handler) {
      this.listeners[name] = handler;
    },
    setAttribute(name, value) {
      this.attributes[name] = value;
    },
    ...extra,
  };
}

function stubEls() {
  return {
    savedViewSelect: el(),
    saveView: el(),
    deleteView: el(),
    searchInput: el(),
    statusFilter: el(),
    labelFilter: el(),
    priorityFilter: el(),
    channelFilter: el(),
    sortFilter: el(),
    ownershipFilter: el(),
    focusWaiting: el(),
  };
}

function configureDeps({ apiResults = {}, filters = {} } = {}) {
  const els = stubEls();
  Object.assign(els.searchInput, { value: filters.search ?? "" });
  Object.assign(els.statusFilter, { value: filters.status ?? "" });
  Object.assign(els.labelFilter, { value: filters.label ?? "" });
  Object.assign(els.priorityFilter, { value: filters.priority ?? "" });
  Object.assign(els.channelFilter, { value: filters.channel ?? "" });
  Object.assign(els.sortFilter, { value: filters.sort ?? "" });
  Object.assign(els.ownershipFilter, { value: filters.ownership ?? "" });
  const calls = [];
  const apiStub = async (url, options) => {
    calls.push({ url, options });
    if (options?.method === "POST") return apiResults.post ?? { id: "view-1" };
    return apiResults.list ?? [{ id: "view-1", name: "视图A" }];
  };
  configure({
    state: { savedViews: [{ id: "view-9", name: "需响应", filters: {} }] },
    els,
    api: apiStub,
    request: async (url, options) => {
      calls.push({ url, options, via: "request" });
      return {};
    },
    showToast: async (message, isError) => calls.push({ toast: message, isError }),
    escapeHtml: (value) => String(value ?? "").replace(/</g, "&lt;").replace(/&/g, "&amp;"),
    refreshAll: () => calls.push({ refreshed: true }),
  });
  return { calls, els };
}

beforeEach(() => {
  delete globalThis.window;
});

test("saveSavedView posts the trimmed name with the current filter snapshot", async () => {
  globalThis.window = new (class extends EventTarget {})();
  const { calls, els } = configureDeps({ filters: { status: "needs_response", search: "  发票  " } });
  const id = await saveSavedView("  需响应视图  ");
  assert.equal(id, "view-1");
  const post = calls.find((call) => call.url === "/api/saved-views");
  assert.deepEqual(JSON.parse(post.options.body), {
    name: "需响应视图",
    filters: { search: "发票", status: "needs_response" },
  });
  assert.ok(calls.some((call) => call.toast === "视图已保存"));
  assert.equal(els.savedViewSelect.value, "view-1");
  assert.equal(els.deleteView.disabled, false);
});

test("saveSavedView failures toast and return null", async () => {
  globalThis.window = new (class extends EventTarget {})();
  const { calls } = configureDeps({
    apiResults: { post: Promise.reject(new Error("boom")) },
  });
  const id = await saveSavedView("x");
  assert.equal(id, null);
  assert.ok(calls.some((call) => call.toast === "boom" && call.isError));
});

test("deleteSavedView uses the raw request path and reports ok", async () => {
  globalThis.window = new (class extends EventTarget {})();
  const { calls } = configureDeps();
  const ok = await deleteSavedView("view-9");
  assert.equal(ok, true);
  const del = calls.find((call) => call.via === "request");
  assert.equal(del.url, "/api/saved-views/view-9");
  assert.equal(del.options.method, "DELETE");
  assert.ok(calls.some((call) => call.toast === "视图已删除"));
});

test("deleteSavedView rejects empty ids without a request", async () => {
  globalThis.window = new (class extends EventTarget {})();
  const { calls } = configureDeps();
  const ok = await deleteSavedView("");
  assert.equal(ok, false);
  assert.equal(calls.length, 0);
});

test("bindSavedViews wires the legacy controls and island bridges", async () => {
  const windowStub = new (class extends EventTarget {})();
  windowStub.prompt = () => "island 视图";
  globalThis.window = windowStub;
  const dispatches = [];
  globalThis.window.addEventListener("helix-saved-views-changed", (event) =>
    dispatches.push(event.detail),
  );
  const { calls, els } = configureDeps();
  assert.equal(bindSavedViews(), true);
  // Legacy select change applies the matching view.
  els.savedViewSelect.value = "view-9";
  els.savedViewSelect.listeners.change();
  assert.ok(calls.some((call) => call.refreshed));
  // Island save bridge reports the created id back.
  globalThis.window.dispatchEvent(
    new CustomEvent("helix-saved-views-save", { detail: { name: "island 视图" } }),
  );
  await new Promise((resolve) => setTimeout(resolve, 30));
  // Island delete bridge reports ok.
  globalThis.window.dispatchEvent(
    new CustomEvent("helix-saved-views-delete", { detail: { id: "view-1" } }),
  );
  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.deepEqual(dispatches, [{ ok: true, id: "view-1" }, { ok: true }]);
});

// ---- filter snapshot / select render / reload / apply (slice 23) ----

test("currentViewFilters drops empty inputs and the default priority sort", () => {
  const { els } = configureDeps({
    filters: { search: "  ", status: "", label: "账单", priority: "high", sort: "priority" },
  });
  assert.deepEqual(currentViewFilters(), { label: "账单", priority: "high" });
  els.sortFilter.value = "updated";
  els.searchInput.value = "  退款  ";
  assert.deepEqual(currentViewFilters(), {
    search: "退款",
    label: "账单",
    priority: "high",
    sort: "updated",
  });
});

test("renderSavedViews escapes names and keeps a still-valid selection", () => {
  const { els } = configureDeps();
  renderSavedViews();
  assert.ok(els.savedViewSelect.innerHTML.includes('value="view-9"'));
  assert.ok(els.savedViewSelect.innerHTML.includes("需响应"));
  assert.equal(els.savedViewSelect.value, "");
  assert.equal(els.deleteView.disabled, true);

  els.savedViewSelect.value = "view-9";
  renderSavedViews();
  assert.equal(els.savedViewSelect.value, "view-9", "a surviving selection is restored");
  assert.equal(els.deleteView.disabled, false);

  els.savedViewSelect.value = "gone";
  renderSavedViews();
  assert.equal(els.savedViewSelect.value, "", "a deleted selection falls back to the placeholder");

  const { els: escaped } = configureDeps();
  configure({
    state: { savedViews: [{ id: "v<script>", name: "<b>x</b>" }] },
    els: escaped,
    api: async () => [],
    request: async () => ({}),
    showToast: async () => {},
    escapeHtml: (value) => String(value ?? "").replace(/</g, "&lt;").replace(/&/g, "&amp;"),
    refreshAll: () => {},
  });
  renderSavedViews();
  assert.ok(!escaped.savedViewSelect.innerHTML.includes("<script>"), "ids and names are escaped");
});

test("loadSavedViews stores the list, renders it, and degrades on failure", async () => {
  globalThis.window = new (class extends EventTarget {})();
  const { calls, els } = configureDeps({ apiResults: { list: [{ id: "v-2", name: "B" }] } });
  await loadSavedViews();
  assert.deepEqual(calls.map((call) => call.url), ["/api/saved-views"]);
  assert.ok(els.savedViewSelect.innerHTML.includes('value="v-2"'));

  const failing = configureDeps();
  configure({
    state: { savedViews: [] },
    els: failing.els,
    api: async () => {
      throw new Error("offline");
    },
    request: async () => ({}),
    showToast: async () => {},
    escapeHtml: (value) => String(value ?? ""),
    refreshAll: () => {},
  });
  await loadSavedViews();
  assert.ok(failing.els.savedViewSelect.innerHTML.includes("保存的视图"), "still renders on error");
});

test("loadSavedViews is a no-op when the shell island owns the select", async () => {
  const windowStub = new (class extends EventTarget {})();
  windowStub.__HELIX_ISLAND_MODE__ = true;
  globalThis.window = windowStub;
  const { calls } = configureDeps();
  await loadSavedViews();
  assert.equal(calls.length, 0, "island mode never fetches the legacy list");
});

test("applySavedView writes the filters back onto the inputs and refreshes", () => {
  const { calls, els } = configureDeps();
  applySavedView({
    filters: {
      search: "发票",
      status: "waiting_human",
      label: "账单",
      priority: "high",
      channel: "email",
      sort: "updated",
      ownership: "needs_response",
    },
  });
  assert.equal(els.searchInput.value, "发票");
  assert.equal(els.statusFilter.value, "waiting_human");
  assert.equal(els.labelFilter.value, "账单");
  assert.equal(els.priorityFilter.value, "high");
  assert.equal(els.channelFilter.value, "email");
  assert.equal(els.sortFilter.value, "updated");
  assert.equal(els.ownershipFilter.value, "needs_response");
  assert.equal(els.focusWaiting.attributes["aria-pressed"], "true");
  assert.ok(calls.some((call) => call.refreshed));

  const empty = configureDeps();
  applySavedView(undefined);
  assert.equal(empty.els.sortFilter.value, "priority", "missing filters reset sort to the default");
  assert.equal(empty.els.focusWaiting.attributes["aria-pressed"], "false");
});
