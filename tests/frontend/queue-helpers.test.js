// Helix Support — queue helper unit tests (app.js <500 campaign)
// Run: node --test tests/frontend/queue-helpers.test.js

import { afterEach, beforeEach, test } from "node:test";
import assert from "node:assert/strict";

import {
  configure,
  conversationQuery,
  loadLabelCatalog,
  queueSignature,
} from "../../app/static/js/queue-helpers.js";

function stubEls(overrides = {}) {
  const el = (value = "") => ({ value, ...overrides });
  return {
    searchInput: el(""),
    statusFilter: el(""),
    labelFilter: el(""),
    priorityFilter: el(""),
    channelFilter: el(""),
    sortFilter: el(""),
    ownershipFilter: el(""),
  };
}

function stubCtx({ els, state, queuePageSize, api } = {}) {
  const ctx = {
    els: els || stubEls(),
    state: state || { me: {}, labelsLoadedAt: 0, labelCatalog: [] },
    api: api || (async () => []),
    queuePageSize: queuePageSize || (() => 50),
  };
  configure(ctx);
  return ctx;
}

beforeEach(() => {
  // None of these helpers touch window; keep a real global for safety.
});
afterEach(() => {
  configure(null);
});

test("conversationQuery emits an empty query with only the default limit", () => {
  stubCtx();
  const params = new URLSearchParams(conversationQuery());
  assert.equal(params.toString(), "limit=50");
});

test("conversationQuery maps every filter control onto its param", () => {
  const els = stubEls();
  els.searchInput.value = "退款";
  els.statusFilter.value = "open";
  els.labelFilter.value = "billing";
  els.priorityFilter.value = "high";
  els.channelFilter.value = "web";
  els.sortFilter.value = "priority";
  stubCtx({ els });
  const params = new URLSearchParams(conversationQuery());
  assert.equal(params.get("search"), "退款");
  assert.equal(params.get("status"), "open");
  assert.equal(params.get("label"), "billing");
  assert.equal(params.get("priority"), "high");
  assert.equal(params.get("channel"), "web");
  assert.equal(params.get("sort"), "priority");
  assert.equal(params.get("limit"), "50");
});

test("conversationQuery maps ownership onto mine/unassigned/unclaimed", () => {
  const els = stubEls();
  els.ownershipFilter.value = "unassigned";
  stubCtx({ els });
  assert.equal(new URLSearchParams(conversationQuery()).get("unassigned"), "true");

  els.ownershipFilter.value = "claimed_by_me";
  stubCtx({ els, state: { me: { actor_id: "op-9" }, labelsLoadedAt: 0, labelCatalog: [] } });
  assert.equal(new URLSearchParams(conversationQuery()).get("claimed_by"), "op-9");

  els.ownershipFilter.value = "claimed_by_me";
  stubCtx({ els, state: { me: {}, labelsLoadedAt: 0, labelCatalog: [] } });
  assert.equal(new URLSearchParams(conversationQuery()).get("claimed_by"), null);

  els.ownershipFilter.value = "sla_breached";
  stubCtx({ els });
  assert.equal(new URLSearchParams(conversationQuery()).get("sla_breached"), "true");
});

test("loadLabelCatalog uses the cached catalog within the 60s window", async () => {
  const calls = [];
  const state = { labelsLoadedAt: Date.now(), labelCatalog: ["billing"] };
  const ctx = stubCtx({ state, api: async () => { calls.push(1); return ["shipping"]; } });
  const result = await loadLabelCatalog();
  assert.deepEqual(result, ["billing"]);
  assert.equal(calls.length, 0, "no refetch while fresh");
});

test("loadLabelCatalog refetches when stale or forced", async () => {
  let calls = 0;
  const state = { labelsLoadedAt: Date.now() - 120000, labelCatalog: ["stale"] };
  const ctx = stubCtx({ state, api: async () => { calls += 1; return ["fresh"]; } });
  assert.deepEqual(await loadLabelCatalog(), ["fresh"]);
  assert.deepEqual(state.labelCatalog, ["fresh"]);
  assert.equal(calls, 1);

  // force bypasses the cache window entirely.
  calls = 0;
  state.labelsLoadedAt = Date.now();
  state.labelCatalog = ["cached"];
  assert.deepEqual(await loadLabelCatalog({ force: true }), ["fresh"]);
  assert.equal(calls, 1, "force refetches but returns the fresh value");
});

test("loadLabelCatalog fetches when the cache is empty even if recent", async () => {
  let calls = 0;
  const state = { labelsLoadedAt: Date.now(), labelCatalog: [] };
  stubCtx({ state, api: async () => { calls += 1; return ["x"]; } });
  await loadLabelCatalog();
  assert.equal(calls, 1, "empty catalog never trusts the timestamp");
});

test("queueSignature captures status/version/timestamps and claim bits", () => {
  const rows = [
    { id: "c1", version: 2, updated_at: "t1", status: "open", claim_active: true, needs_response: true },
    { id: "c2", version: 0, updated_at: "", status: "resolved", claim_active: false, needs_response: false },
  ];
  const sig = queueSignature(rows);
  assert.equal(sig, "c1:2:t1:open:1:1|c2:0::resolved:0:0");
});

test("queueSignature reflects need_response so the queue re-renders", () => {
  const base = { id: "c1", version: 1, updated_at: "t", status: "open", claim_active: false };
  const a = queueSignature([{ ...base, needs_response: false }]);
  const b = queueSignature([{ ...base, needs_response: true }]);
  assert.notEqual(a, b);
});