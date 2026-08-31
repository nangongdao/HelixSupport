// Helix Support — nav DOM lifecycle unit tests (app.js <500 campaign)
// Run: node --test tests/frontend/app-nav.test.js

import { afterEach, beforeEach, test } from "node:test";
import assert from "node:assert/strict";

import {
  configure,
  currentAppView,
  setNavActive,
  showAppView,
  switchAppView,
} from "../../app/static/js/app-nav.js";

function makeEl() {
  return {
    hidden: false,
    dataset: {},
    classList: {
      _classes: new Set(),
      toggle(name, force) {
        if (force) this._classes.add(name);
        else this._classes.delete(name);
      },
      contains(name) {
        return this._classes.has(name);
      },
    },
  };
}

function stubCtx(overrides = {}) {
  const els = {
    navItems: [makeEl(), makeEl(), makeEl()],
    workspaceView: makeEl(),
    qualityView: makeEl(),
    knowledgeView: makeEl(),
    adminView: makeEl(),
    placeholderView: makeEl(),
    qualityViewBuckets: {},
    qualityViewGaps: {},
  };
  els.navItems[0].dataset.view = "workspace";
  els.navItems[1].dataset.view = "quality";
  els.navItems[2].dataset.view = "admin";

  const ctx = {
    els,
    loadDesktopInfo: () => {},
    renderQualityPanel: () => {},
    loadQualityPanel: () => {},
    loadAdminView: () => {},
    loadKnowledgeView: () => {},
    scheduleIdle: (fn) => fn(),
    ...overrides,
  };
  configure(ctx);
  return ctx;
}

let originalWindow;
beforeEach(() => {
  originalWindow = globalThis.window;
  globalThis.window = { __HELIX_ISLAND_MODE__: false, dispatchEvent: () => true };
});
afterEach(() => {
  configure(null);
  if (originalWindow === undefined) delete globalThis.window;
  else globalThis.window = originalWindow;
});

test("setNavActive marks exactly the matching rail item is-active", () => {
  const { els } = stubCtx();
  setNavActive("quality");
  assert.equal(els.navItems[0].classList.contains("is-active"), false);
  assert.equal(els.navItems[1].classList.contains("is-active"), true);
  assert.equal(els.navItems[2].classList.contains("is-active"), false);
});

test("showAppView reveals the target mount and hides the others", () => {
  const { els } = stubCtx();
  showAppView("admin");
  assert.equal(els.workspaceView.hidden, true);
  assert.equal(els.qualityView.hidden, true);
  assert.equal(els.knowledgeView.hidden, true);
  assert.equal(els.adminView.hidden, false);
  // settings is a placeholder — nothing else hidden except the placeholder.
  showAppView("settings");
  assert.equal(els.workspaceView.hidden, true);
  assert.equal(els.placeholderView.hidden, false);
});

test("showAppView loads desktop info only when the settings placeholder shows", () => {
  let calls = 0;
  stubCtx({ loadDesktopInfo: () => { calls += 1; } });
  showAppView("workspace");
  assert.equal(calls, 0);
  showAppView("settings");
  assert.equal(calls, 1);
});

test("switchAppView workspace hides every view and no loaders run", () => {
  const calls = { admin: 0, knowledge: 0, quality: 0 };
  const { els } = stubCtx({
    loadAdminView: () => { calls.admin += 1; },
    loadKnowledgeView: () => { calls.knowledge += 1; },
    loadQualityPanel: () => { calls.quality += 1; },
  });
  switchAppView("workspace");
  assert.equal(els.workspaceView.hidden, false);
  assert.deepEqual(calls, { admin: 0, knowledge: 0, quality: 0 });
});

test("switchAppView admin/knowledge fan out to their loaders", () => {
  const calls = { admin: 0, knowledge: 0, quality: 0 };
  const ctx = stubCtx({
    loadAdminView: () => { calls.admin += 1; },
    loadKnowledgeView: () => { calls.knowledge += 1; },
    loadQualityPanel: () => { calls.quality += 1; },
  });
  switchAppView("admin");
  switchAppView("knowledge");
  assert.deepEqual(calls, { admin: 1, knowledge: 1, quality: 0 });
});

test("switchAppView quality in browser mode renders + schedules a refresh", () => {
  const calls = { render: 0, quality: 0, idle: 0 };
  const ctx = stubCtx({
    renderQualityPanel: () => { calls.render += 1; },
    loadQualityPanel: () => { calls.quality += 1; },
    scheduleIdle: (fn) => { calls.idle += 1; fn(); },
  });
  switchAppView("quality");
  assert.deepEqual(calls, { render: 1, quality: 1, idle: 1 });
});

test("switchAppView quality in island mode dispatches the refresh event instead", () => {
  let dispatched = null;
  globalThis.window = {
    __HELIX_ISLAND_MODE__: true,
    dispatchEvent: (evt) => { dispatched = evt; },
  };
  const calls = { render: 0, quality: 0 };
  stubCtx({
    renderQualityPanel: () => { calls.render += 1; },
    loadQualityPanel: () => { calls.quality += 1; },
  });
  switchAppView("quality");
  assert.deepEqual(calls, { render: 0, quality: 0 });
  assert.equal(dispatched.type, "helix-quality-refresh");
  assert.deepEqual(dispatched.detail, { force: false });
});

test("currentAppView reads the active rail item, defaulting to workspace", () => {
  const { els } = stubCtx();
  assert.equal(currentAppView(), "workspace");
  setNavActive("admin");
  assert.equal(currentAppView(), "admin");
});
