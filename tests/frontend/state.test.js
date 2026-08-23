// Helix Support — state reducer module unit tests (Phase 26.1)
// Run: node --test tests/frontend/state.test.js

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  upsertConversation,
  queueSignature,
  filterConversations,
  countByStatus,
  latestAssistant,
  normalizeLabels,
} from "../../app/static/js/state.js";

function conv(id, status = "open", updated = "2026-01-01T00:00:00Z", version = 1) {
  return { id, status, updated_at: updated, version };
}

test("upsertConversation appends when absent", () => {
  const next = upsertConversation([], { id: "c1" });
  assert.equal(next.length, 1);
  assert.equal(next[0].id, "c1");
});

test("upsertConversation replaces in place preserving order", () => {
  const list = [conv("a"), conv("b"), conv("c")];
  const next = upsertConversation(list, conv("b", "resolved"));
  assert.equal(next.length, 3);
  assert.deepEqual(next.map((c) => c.id), ["a", "b", "c"]);
  assert.equal(next[1].status, "resolved");
});

test("upsertConversation does not mutate the input array", () => {
  const list = [conv("a")];
  const next = upsertConversation(list, conv("b"));
  assert.equal(list.length, 1);
  assert.equal(next.length, 2);
});

test("queueSignature reflects status/updated/version", () => {
  const a = [conv("c1", "open", "2026-01-01", 1)];
  const b = [conv("c1", "resolved", "2026-01-01", 1)];
  assert.notEqual(queueSignature(a), queueSignature(b));
});

test("queueSignature is stable for identical lists", () => {
  const a = [conv("c1", "open", "2026-01-01", 1), conv("c2", "open", "2026-01-01", 1)];
  const b = [conv("c1", "open", "2026-01-01", 1), conv("c2", "open", "2026-01-01", 1)];
  assert.equal(queueSignature(a), queueSignature(b));
});

test("queueSignature returns empty for non-arrays", () => {
  assert.equal(queueSignature(null), "");
  assert.equal(queueSignature(undefined), "");
});

test("filterConversations applies the predicate", () => {
  const list = [conv("a", "open"), conv("b", "resolved")];
  const open = filterConversations(list, (c) => c.status === "open");
  assert.equal(open.length, 1);
  assert.equal(open[0].id, "a");
});

test("filterConversations returns the list when no filter", () => {
  const list = [conv("a")];
  assert.equal(filterConversations(list), list);
});

test("countByStatus counts each status", () => {
  const list = [conv("a", "open"), conv("b", "waiting_human"), conv("c", "open")];
  assert.deepEqual(countByStatus(list), {
    open: 2,
    waiting_human: 1,
    human_active: 0,
    resolved: 0,
  });
});

test("countByStatus handles empty and null", () => {
  assert.deepEqual(countByStatus([]), { open: 0, waiting_human: 0, human_active: 0, resolved: 0 });
  assert.deepEqual(countByStatus(null), { open: 0, waiting_human: 0, human_active: 0, resolved: 0 });
});

test("latestAssistant returns the last assistant message", () => {
  const messages = [
    { role: "customer", content: "hi" },
    { role: "assistant", content: "a1" },
    { role: "customer", content: "again" },
    { role: "assistant", content: "a2" },
  ];
  assert.equal(latestAssistant(messages).content, "a2");
});

test("latestAssistant returns null without assistants", () => {
  assert.equal(latestAssistant([{ role: "customer", content: "hi" }]), null);
  assert.equal(latestAssistant([]), null);
  assert.equal(latestAssistant(null), null);
});

test("normalizeLabels trims, dedupes, and drops empties", () => {
  assert.deepEqual(normalizeLabels(["  a ", "", "b", "a", " b"]), ["a", "b"]);
});

test("normalizeLabels caps at max", () => {
  const many = Array.from({ length: 30 }, (_, i) => `label-${i}`);
  assert.equal(normalizeLabels(many, 20).length, 20);
});

test("normalizeLabels handles non-array input", () => {
  assert.deepEqual(normalizeLabels(null), []);
  assert.deepEqual(normalizeLabels("x"), []);
});
