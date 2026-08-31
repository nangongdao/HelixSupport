// Helix Support — queue filter controls unit tests (app.js <500 slice 24)
// Run: node --test tests/frontend/queue-filters.test.js

import { afterEach, beforeEach, mock, test } from "node:test";
import assert from "node:assert/strict";

import {
  bindQueueFilters,
  configure,
  renderLabelFilter,
} from "../../app/static/js/queue-filters.js";

/** Element stub that records listeners so a control can be "used" in tests. */
function el(extra = {}) {
  return {
    value: "",
    innerHTML: "",
    listeners: {},
    addEventListener(name, handler) {
      this.listeners[name] = handler;
    },
    fire(name) {
      this.listeners[name]?.();
    },
    ...extra,
  };
}

function stubEls() {
  return {
    refreshList: el(),
    statusFilter: el(),
    labelFilter: el(),
    priorityFilter: el(),
    ownershipFilter: el(),
    channelFilter: el(),
    sortFilter: el(),
    focusWaiting: el(),
    searchInput: el(),
  };
}

function configureDeps({ state = {} } = {}) {
  const els = stubEls();
  const calls = [];
  configure({
    state: { labelCatalog: [], lowPerf: false, ...state },
    els,
    refreshAll: (options) => calls.push({ refreshed: true, options }),
    escapeHtml: (value) => String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;"),
  });
  return { calls, els };
}

beforeEach(() => {
  // window.setTimeout/clearTimeout resolve at call time, so mock.timers
  // installed by a test is picked up by the debounced search handler.
  globalThis.window = {
    setTimeout: (...args) => setTimeout(...args),
    clearTimeout: (...args) => clearTimeout(...args),
  };
});

afterEach(() => {
  mock.timers.reset();
  delete globalThis.window;
});

test("renderLabelFilter renders the catalog and keeps an unknown selection", () => {
  const { els } = configureDeps({
    state: { labelCatalog: [{ label: "账单", conversation_count: 3 }] },
  });
  renderLabelFilter();
  assert.ok(els.labelFilter.innerHTML.includes('value="账单"'));
  assert.ok(els.labelFilter.innerHTML.includes("账单 · 3"));
  assert.equal(els.labelFilter.value, "");

  els.labelFilter.value = "已下线标签";
  renderLabelFilter();
  assert.ok(
    els.labelFilter.innerHTML.includes('value="已下线标签"'),
    "a selection the catalog dropped is re-added so the queue keeps filtering",
  );
  assert.equal(els.labelFilter.value, "已下线标签");
});

test("renderLabelFilter escapes labels and counts before innerHTML", () => {
  const { els } = configureDeps({
    state: { labelCatalog: [{ label: "<img onerror=1>", conversation_count: 0 }] },
  });
  renderLabelFilter();
  assert.ok(!els.labelFilter.innerHTML.includes("<img onerror=1>"));
  assert.ok(els.labelFilter.innerHTML.includes("&lt;img onerror=1>"));
});

test("bindQueueFilters reloads the queue on every filter change", () => {
  const { calls, els } = configureDeps();
  assert.equal(bindQueueFilters(), true);
  for (const name of ["statusFilter", "labelFilter", "priorityFilter", "ownershipFilter", "channelFilter", "sortFilter"]) {
    els[name].fire("change");
  }
  els.refreshList.fire("click");
  assert.equal(calls.length, 7, "six filters + the manual refresh button");
});

test("bindQueueFilters skips optional controls that the page does not ship", () => {
  const { calls, els } = configureDeps();
  delete els.channelFilter;
  delete els.sortFilter;
  bindQueueFilters();
  els.statusFilter.fire("change");
  assert.equal(calls.length, 1);
});

test("the focus toggle flips the ownership filter and reloads", () => {
  const { calls, els } = configureDeps();
  bindQueueFilters();
  els.focusWaiting.fire("click");
  assert.equal(els.ownershipFilter.value, "needs_response");
  els.focusWaiting.fire("click");
  assert.equal(els.ownershipFilter.value, "");
  assert.equal(calls.length, 2);
});

test("the search box debounces into a silent queue-only refresh", () => {
  mock.timers.enable({ apis: ["setTimeout"] });
  const { calls, els } = configureDeps();
  bindQueueFilters();
  els.searchInput.fire("input");
  els.searchInput.fire("input");
  els.searchInput.fire("input");
  assert.equal(calls.length, 0, "typing does not refresh per keystroke");
  mock.timers.tick(260);
  assert.equal(calls.length, 1, "one refresh after the debounce window");
  assert.deepEqual(calls[0].options, { silent: true, refreshDetail: false });
});

test("low-perf widens the search debounce window", () => {
  mock.timers.enable({ apis: ["setTimeout"] });
  const { calls, els } = configureDeps({ state: { lowPerf: true } });
  bindQueueFilters();
  els.searchInput.fire("input");
  mock.timers.tick(300);
  assert.equal(calls.length, 0, "450ms window has not elapsed at 300ms");
  mock.timers.tick(200);
  assert.equal(calls.length, 1);
});
