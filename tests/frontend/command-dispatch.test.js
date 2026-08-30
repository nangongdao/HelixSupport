// Helix Support — palette command dispatcher unit tests (app.js <500 slice 22)
// Run: node --test tests/frontend/command-dispatch.test.js

import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";

import { bindCommandDispatch, checkBackendHealth, configure } from "../../app/static/js/command-dispatch.js";

function installWindow() {
  const stub = new (class extends EventTarget {})();
  globalThis.window = stub;
  return stub;
}

const dispatched = [];

function configureDeps({ fetchImpl } = {}) {
  const calls = [];
  configure({
    state: {},
    els: {},
    showToast: async (message, isError) => calls.push({ toast: message, isError }),
    switchAppView: (view) => calls.push({ view }),
    refreshAll: async () => calls.push({ refreshed: true }),
  });
  if (fetchImpl) {
    globalThis.fetch = async (url) => fetchImpl(url);
  }
  return { calls, dispatched };
}

beforeEach(() => {
  delete globalThis.window;
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
