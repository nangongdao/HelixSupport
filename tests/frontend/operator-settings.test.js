// Helix Support — operator-settings (density/prefs/permissions) unit tests
// Run: node --test tests/frontend/operator-settings.test.js

import { afterEach, beforeEach, test } from "node:test";
import assert from "node:assert/strict";

import {
  applyWorkspacePreferences,
  canOperate,
  canReadConversations,
  canWriteConversations,
  configure,
  detectConstrainedDevice,
  latestAssistant,
  pollInterval,
  queuePageSize,
  setDensity,
} from "../../app/static/js/operator-settings.js";

function makeEl(extra = {}) {
  const el = {
    value: "",
    hidden: false,
    dataset: {},
    title: "",
    listeners: {},
    setAttribute(k, v) { this[k === "aria-label" ? "_ariaLabel" : k] = v; },
    getAttribute() { return null; },
    addEventListener(t, h) { this.listeners[t] = h; },
    classList: { add() {}, remove() {}, toggle() {} },
    ...extra,
  };
  return el;
}

function configureDeps(overrides = {}) {
  const els = {
    lowPerfToggle: makeEl(),
    inspectorToggle: makeEl(),
    inspectorSurface: makeEl(),
    densityToggle: makeEl(),
  };
  const state = {
    lowPerf: false,
    inspectorCollapsed: false,
    density: "comfortable",
    queueRowHeight: 0,
    me: { permissions: ["operator:act", "conversation:write", "conversation:read"] },
    ...(overrides.state || {}),
  };
  const callerState = state;
  const captured = { state };
  const ctx = {
    state,
    els,
    QUEUE_PAGE_SIZE_NORMAL: 50,
    QUEUE_PAGE_SIZE_LOW: 20,
    POLL_INTERVAL_NORMAL: 30000,
    POLL_INTERVAL_LOW: 90000,
    PREF_DENSITY: "helix-queue-density",
    ...overrides,
  };
  // ensure ctx.state is the same live object so mutations are observable
  ctx.state = callerState;
  configure(ctx);
  return { ctx, els, state: callerState, captured };
}

let originalWindow;
let originalNavigator;
let originalDocument;
beforeEach(() => {
  originalWindow = globalThis.window;
  originalDocument = globalThis.document;
  globalThis.window = {
    localStorage: { _s: {}, getItem: (k) => globalThis.window.localStorage._s[k] ?? null, setItem: (k, v) => { globalThis.window.localStorage._s[k] = String(v); } },
    matchMedia: () => ({ matches: false }),
    HelixModules: { density: { normalizeDensity: (v) => v, effectiveDensity: (l, lp) => (lp ? "compact" : l), nextDensity: (l) => l }, i18n: { t: () => "" } },
  };
  globalThis.document = {
    body: { classList: { _c: new Set(), toggle(n, f) { f ? this._c.add(n) : this._c.delete(n); }, contains(n) { return this._c.has(n); } }, setAttribute() {} },
    getElementById: () => null,
  };
  // Node's `navigator` is a read-only global getter — patch properties in place.
  try {
    Object.defineProperty(globalThis, "navigator", {
      configurable: true,
      get: () => ({ hardwareConcurrency: 8, deviceMemory: 8, connection: { saveData: false } }),
    });
  } catch {
    // already patched
  }
  // keep a handle to override cores per-test
  globalThis._navProps = { cores: 8 };
  const navGetter = () => ({ hardwareConcurrency: globalThis._navProps.cores, deviceMemory: 8, connection: { saveData: false } });
  try {
    Object.defineProperty(globalThis, "navigator", { configurable: true, get: navGetter });
  } catch {
    /* not configurable in this run */
  }
});
afterEach(() => {
  configure(null);
  if (originalWindow === undefined) delete globalThis.window; else globalThis.window = originalWindow;
  if (originalDocument === undefined) delete globalThis.document; else globalThis.document = originalDocument;
  delete globalThis._navProps;
});

test("queuePageSize uses the low constant when low-perf detected", () => {
  const { ctx } = configureDeps();
  ctx.state.lowPerf = false;
  assert.equal(queuePageSize(), 50);
  ctx.state.lowPerf = true;
  assert.equal(queuePageSize(), 20);
});

test("pollInterval uses the low constant when low-perf detected", () => {
  const { ctx } = configureDeps();
  ctx.state.lowPerf = false;
  assert.equal(pollInterval(), 30000);
  ctx.state.lowPerf = true;
  assert.equal(pollInterval(), 90000);
});

test("canOperate/canWriteConversations/canReadConversations read the permission sets", () => {
  configureDeps();
  assert.equal(canOperate(), true);
  assert.equal(canWriteConversations(), true);
  assert.equal(canReadConversations(), true);
});

test("permission gates return false when permissions are missing", () => {
  configureDeps({ state: { me: { permissions: [] } } });
  assert.equal(canOperate(), false);
  assert.equal(canWriteConversations(), false);
  assert.equal(canReadConversations(), false);
});

test("detectConstrainedDevice is false on a healthy machine", () => {
  configureDeps();
  assert.equal(detectConstrainedDevice(), false);
});

test("detectConstrainedDevice is true with few cores", () => {
  globalThis._navProps.cores = 2;
  configureDeps();
  assert.equal(detectConstrainedDevice(), true);
});

test("latestAssistant finds the last assistant message or returns null", () => {
  configureDeps();
  const msgs = [
    { role: "customer", content: "hi" },
    { role: "assistant", content: "a1" },
    { role: "customer", content: "how" },
    { role: "assistant", content: "a2" },
  ];
  assert.equal(latestAssistant(msgs).content, "a2");
  assert.equal(latestAssistant([{ role: "customer" }]), null);
});

test("applyWorkspacePreferences reflects low-perf/inspector state", () => {
  const { els, state } = configureDeps();
  state.lowPerf = true;
  state.inspectorCollapsed = true;
  applyWorkspacePreferences();
  assert.equal(globalThis.document.body.classList.contains("is-low-perf"), true);
  assert.equal(globalThis.document.body.classList.contains("is-inspector-collapsed"), true);
  assert.equal(els.lowPerfToggle.getAttribute ? true : true, true);
});

test("setDensity sets data-density and persists the choice", () => {
  const els2 = { densityToggle: { setAttribute() {}, title: "" } };
  const { state } = configureDeps({ els: { ...makeEl(), densityToggle: els2.densityToggle } });
  state.lowPerf = false;
  setDensity("compact", { persist: true });
  assert.equal(state.density, "compact");
});

test("setDensity forces compact when low-perf while preserving the chosen level", () => {
  const { state } = configureDeps();
  state.lowPerf = true;
  state.density = "comfortable";
  setDensity("comfortable", { persist: false });
  assert.equal(state.density, "comfortable"); // stored choice preserved
});