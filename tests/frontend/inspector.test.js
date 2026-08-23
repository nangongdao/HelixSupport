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
