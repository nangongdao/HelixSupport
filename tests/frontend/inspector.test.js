// Helix Support — inspector tab-machine unit tests (ROADMAP §43.6)
// Run: node --test tests/frontend/inspector.test.js

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  INSPECTOR_TABS,
  createInspectorState,
  reduceInspector,
  safeCitationUrl,
} from "../../app/static/js/inspector.js";

test("INSPECTOR_TABS covers the four mount points in display order", () => {
  assert.deepEqual([...INSPECTOR_TABS], ["overview", "evidence", "audit", "quality"]);
});

test("createInspectorState starts on overview with nothing rendered", () => {
  const state = createInspectorState();
  assert.equal(state.activeTab, "overview");
  assert.equal(state.collapsed, false);
  assert.deepEqual(state.rendered, { overview: false, evidence: false, audit: false });
});

test("reduceInspector select-tab switches and rejects unknown tabs", () => {
  const state = createInspectorState();
  const next = reduceInspector(state, { type: "select-tab", tab: "evidence" });
  assert.equal(next.activeTab, "evidence");
  assert.equal(state.activeTab, "overview"); // original untouched
  const bad = reduceInspector(state, { type: "select-tab", tab: "nope" });
  assert.equal(bad, state); // no-op returns same reference
});

test("mark-rendered is idempotent per tab and scoped to data tabs", () => {
  let state = createInspectorState();
  state = reduceInspector(state, { type: "mark-rendered", tab: "overview" });
  assert.equal(state.rendered.overview, true);
  const again = reduceInspector(state, { type: "mark-rendered", tab: "overview" });
  assert.equal(again, state); // already rendered — no-op
  const quality = reduceInspector(state, { type: "mark-rendered", tab: "quality" });
  assert.equal(quality, state); // quality self-fetches; not a data tab
});

test("invalidate clears every data tab at once", () => {
  let state = createInspectorState();
  for (const tab of ["overview", "evidence", "audit"]) {
    state = reduceInspector(state, { type: "mark-rendered", tab });
  }
  const cleared = reduceInspector(state, { type: "invalidate" });
  assert.deepEqual(cleared.rendered, { overview: false, evidence: false, audit: false });
});

test("invalidate is a no-op when nothing was rendered", () => {
  const state = createInspectorState();
  assert.equal(reduceInspector(state, { type: "invalidate" }), state);
});

test("set-collapsed toggles only with a boolean change", () => {
  const state = createInspectorState();
  const collapsed = reduceInspector(state, { type: "set-collapsed", collapsed: true });
  assert.equal(collapsed.collapsed, true);
  assert.equal(reduceInspector(collapsed, { type: "set-collapsed", collapsed: true }), collapsed);
  assert.equal(reduceInspector(state, { type: "set-collapsed" }), state);
});

test("unknown actions return the same state object", () => {
  const state = createInspectorState();
  assert.equal(reduceInspector(state, { type: "explode" }), state);
});

test("safeCitationUrl allows root-relative and https targets only", () => {
  assert.equal(safeCitationUrl("/static/knowledge/a.md"), "/static/knowledge/a.md");
  assert.equal(safeCitationUrl("https://docs.example/x"), "https://docs.example/x");
  assert.equal(safeCitationUrl("http://docs.example/x"), "#");
  assert.equal(safeCitationUrl("javascript:alert(1)"), "#");
  assert.equal(safeCitationUrl(""), "#");
  assert.equal(safeCitationUrl(undefined), "#");
});

// ── submitNote core (D3 long tail slice 16) ──

import { configure as configureInspector, submitNote } from "../../app/static/js/inspector.js";

function configureForNotes({ api, showToast, loadDetail, refreshAll }) {
  configureInspector({
    state: { selectedId: "conv-1" },
    api,
    showToast: showToast || (async () => {}),
    loadDetail: loadDetail || (async () => {}),
    refreshAll: refreshAll || (async () => {}),
  });
}

test("submitNote posts the trimmed note, toasts and refreshes the detail", async () => {
  const calls = [];
  configureForNotes({
    api: async (url, options) => {
      calls.push({ url, options });
      return {};
    },
    showToast: async (message) => calls.push({ toast: message }),
    loadDetail: async (id) => calls.push({ loadDetail: id }),
    refreshAll: async (options) => calls.push({ refreshAll: options }),
  });
  const ok = await submitNote({ content: "  核对完毕  " });
  assert.equal(ok, true);
  const post = calls.find((call) => call.url);
  assert.match(post.url, /\/api\/conversations\/conv-1\/notes$/);
  assert.deepEqual(JSON.parse(post.options.body), { content: "核对完毕" });
  assert.ok(calls.some((call) => call.toast === "内部备注已添加"));
  assert.ok(calls.some((call) => call.loadDetail === "conv-1"));
  assert.ok(calls.some((call) => call.refreshAll && call.refreshAll.silent === true));
});

test("submitNote rejects empty content without posting", async () => {
  const calls = [];
  configureForNotes({
    api: async (url) => {
      calls.push({ url });
      return {};
    },
  });
  const ok = await submitNote({ content: "   " });
  assert.equal(ok, false);
  assert.equal(calls.length, 0);
});

test("submitNote reports failures without throwing", async () => {
  const calls = [];
  configureForNotes({
    api: async () => {
      throw new Error("boom");
    },
    showToast: async (message) => calls.push({ toast: message }),
  });
  const ok = await submitNote({ content: "内容" });
  assert.equal(ok, false);
  assert.ok(calls.some((call) => call.toast === "boom"));
});
