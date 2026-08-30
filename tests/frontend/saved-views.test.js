// Helix Support — saved-views lifecycle unit tests (app.js <500 slice 21)
// Run: node --test tests/frontend/saved-views.test.js

import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";

import {
  bindSavedViews,
  configure,
  deleteSavedView,
  saveSavedView,
} from "../../app/static/js/saved-views.js";

function stubEls() {
  const el = () => ({
    value: "",
    disabled: true,
    listeners: {},
    addEventListener(name, handler) {
      this.listeners[name] = handler;
    },
  });
  return {
    savedViewSelect: el(),
    saveView: el(),
    deleteView: el(),
  };
}

function configureDeps({ apiCalls = [], apiResults = [] } = {}) {
  const els = stubEls();
  const calls = [];
  let apiIndex = 0;
  const apiStub = async (url, options) => {
    calls.push({ url, options });
    const result = apiResults[apiIndex] ?? { id: "view-1" };
    apiIndex += 1;
    return result;
  };
  configure({
    state: { savedViews: [{ id: "view-9", filters: {} }] },
    els,
    api: apiStub,
    request: async (url, options) => {
      calls.push({ url, options, via: "request" });
      return {};
    },
    showToast: async (message, isError) => calls.push({ toast: message, isError }),
    currentViewFilters: () => ({ status: "needs_response" }),
    applySavedView: (view) => calls.push({ applied: view }),
    loadSavedViews: async () => calls.push({ reloaded: true }),
  });
  return { calls, els };
}

beforeEach(() => {
  delete globalThis.window;
});

test("saveSavedView posts the trimmed name with the current filter snapshot", async () => {
  globalThis.window = new (class extends EventTarget {})();
  const { calls, els } = configureDeps();
  const id = await saveSavedView("  需响应视图  ");
  assert.equal(id, "view-1");
  const post = calls.find((call) => call.url === "/api/saved-views");
  assert.deepEqual(JSON.parse(post.options.body), {
    name: "需响应视图",
    filters: { status: "needs_response" },
  });
  assert.ok(calls.some((call) => call.reloaded));
  assert.equal(els.savedViewSelect.value, "view-1");
  assert.equal(els.deleteView.disabled, false);
  assert.ok(calls.some((call) => call.toast === "视图已保存"));
});

test("saveSavedView failures toast and return null", async () => {
  globalThis.window = new (class extends EventTarget {})();
  const { calls } = configureDeps({
    apiResults: [Promise.reject(new Error("boom"))],
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
  assert.ok(calls.some((call) => call.applied && call.applied.id === "view-9"));
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
