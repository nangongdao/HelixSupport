// Helix Support — queue virtualization module unit tests (ROADMAP §18.4)
// Run: node --test tests/frontend/vqueue.test.js

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  VIRTUAL_THRESHOLD,
  ROW_HEIGHT_ESTIMATES,
  estimatedRowHeight,
  computeWindow,
  signatureOf,
  diffRows,
  rowHeightFromElement,
} from "../../app/static/js/vqueue.js";

function conv(id, status = "open", updated = "2026-01-01T00:00:00Z", version = 1) {
  return { id, status, updated_at: updated, version };
}

test("VIRTUAL_THRESHOLD is 200 and estimates cover the three densities", () => {
  assert.equal(VIRTUAL_THRESHOLD, 200);
  assert.deepEqual(Object.keys(ROW_HEIGHT_ESTIMATES).sort(), ["compact", "dense", "normal"]);
  assert.ok(ROW_HEIGHT_ESTIMATES.normal > ROW_HEIGHT_ESTIMATES.compact);
  assert.ok(ROW_HEIGHT_ESTIMATES.compact > ROW_HEIGHT_ESTIMATES.dense);
});

test("estimatedRowHeight picks per-density estimates", () => {
  assert.equal(estimatedRowHeight(), ROW_HEIGHT_ESTIMATES.normal);
  assert.equal(estimatedRowHeight("normal"), ROW_HEIGHT_ESTIMATES.normal);
  assert.equal(estimatedRowHeight("compact"), ROW_HEIGHT_ESTIMATES.compact);
  assert.equal(estimatedRowHeight("dense"), ROW_HEIGHT_ESTIMATES.dense);
});

test("estimatedRowHeight forces compact under lowPerf", () => {
  assert.equal(estimatedRowHeight("dense", true), ROW_HEIGHT_ESTIMATES.compact);
  assert.equal(estimatedRowHeight("normal", true), ROW_HEIGHT_ESTIMATES.compact);
});

test("computeWindow empty list yields an empty window", () => {
  const w = computeWindow({ total: 0, scrollTop: 0, viewport: 600, rowHeight: 118 });
  assert.equal(w.last, -1);
  assert.equal(w.count, 0);
  assert.equal(w.topPad, 0);
  assert.equal(w.bottomPad, 0);
});

test("computeWindow renders the whole small list", () => {
  const w = computeWindow({ total: 5, scrollTop: 0, viewport: 600, rowHeight: 118 });
  assert.equal(w.first, 0);
  assert.equal(w.last, 4);
  assert.equal(w.count, 5);
  assert.equal(w.topPad, 0);
  assert.equal(w.bottomPad, 0);
});

test("computeWindow window centers on the scrollport mid-scale", () => {
  const total = 1000;
  const rowHeight = 118;
  const viewport = 600;
  const overscan = 4;
  const scrollTop = 2360; // row 20 at top of viewport
  const w = computeWindow({ total, scrollTop, viewport, rowHeight, overscan });
  assert.equal(w.first, 20 - overscan);
  // last = ceil((2360+600)/118) + overscan - 1 = 26 + 4 - 1
  assert.equal(w.last, 29);
  assert.equal(w.topPad, 16 * rowHeight);
  assert.equal(w.bottomPad, (total - 1 - 29) * rowHeight);
  assert.equal(w.count, w.last - w.first + 1);
});

test("computeWindow overscan 0 highlights exactly the viewport band", () => {
  const total = 1000;
  const rowHeight = 118;
  const viewport = 118 * 4;
  const scrollTop = 118 * 10;
  const w = computeWindow({ total, scrollTop, viewport, rowHeight, overscan: 0 });
  assert.equal(w.first, 10);
  assert.equal(w.last, 13);
  assert.equal(w.topPad, 118 * 10);
});

test("computeWindow guards degenerate rowHeight and scrollTop", () => {
  const w = computeWindow({ total: 100, scrollTop: -5, viewport: 0, rowHeight: 0 });
  assert.equal(w.first, 0);
  assert.ok(w.count >= 1);
  // default rowHeight 1 keeps pads finite
  assert.ok(Number.isFinite(w.bottomPad));
});

test("computeWindow clamps the last row to the list end", () => {
  const w = computeWindow({ total: 50, scrollTop: 0, viewport: 100000, rowHeight: 118 });
  assert.equal(w.last, 49);
  assert.equal(w.count, 50);
  assert.equal(w.bottomPad, 0);
});

test("signatureOf is per-id stable content fingerprint", () => {
  assert.equal(signatureOf(conv("a")), "a:open:2026-01-01T00:00:00Z:1");
  assert.equal(signatureOf(conv("a", "resolved", "2026-01-01T00:00:00Z", 2)), "a:resolved:2026-01-01T00:00:00Z:2");
  assert.notEqual(
    signatureOf(conv("a")),
    signatureOf(conv("b")),
  );
  assert.equal(signatureOf(null), "");
  assert.equal(signatureOf({}), "");
});

test("diffRows starts from an empty column", () => {
  const d = diffRows(new Map(), [conv("a"), conv("b")]);
  assert.deepEqual(d.adds.map((c) => c.id), ["a", "b"]);
  assert.deepEqual(d.updates, []);
  assert.deepEqual(d.removes, []);
  assert.deepEqual(d.keeps, []);
});

test("diffRows keeps unchanged rows in place", () => {
  const rendered = new Map([
    ["a", signatureOf(conv("a"))],
    ["b", signatureOf(conv("b"))],
  ]);
  const d = diffRows(rendered, [conv("a"), conv("b")]);
  assert.deepEqual(d.keeps, ["a", "b"]);
  assert.deepEqual(d.adds, []);
  assert.deepEqual(d.updates, []);
  assert.deepEqual(d.removes, []);
});

test("diffRows marks only changed rows as updates", () => {
  const rendered = new Map([
    ["a", signatureOf(conv("a"))],
    ["b", signatureOf(conv("b", "open", "2026-01-01T00:00:00Z", 1))],
    ["c", signatureOf(conv("c"))],
  ]);
  const d = diffRows(rendered, [conv("a"), conv("b", "resolved", "2026-01-01T01:00:00Z", 2), conv("c")]);
  assert.deepEqual(d.updates.map((c) => c.id), ["b"]);
  assert.deepEqual(d.keeps, ["a", "c"]);
});

test("diffRows detects removals and additions in one pass", () => {
  const rendered = new Map([
    ["a", signatureOf(conv("a"))],
    ["gone", signatureOf(conv("gone"))],
  ]);
  const d = diffRows(rendered, [conv("a"), conv("brand-new")]);
  assert.deepEqual(d.removes, ["gone"]);
  assert.deepEqual(d.adds.map((c) => c.id), ["brand-new"]);
  assert.deepEqual(d.keeps, ["a"]);
});

test("diffRows treats a plain array (no Map) as empty", () => {
  const d = diffRows([conv("x")], [conv("y")]);
  assert.deepEqual(d.adds.map((c) => c.id), ["y"]);
});

test("rowHeightFromElement returns offsetHeight plus vertical margin", () => {
  const el = { offsetHeight: 110 };
  const getComputed = () => ({ marginBottom: "8px" });
  assert.equal(rowHeightFromElement(el, getComputed), 118);
});

test("rowHeightFromElement tolerates missing margin and zero height", () => {
  const el = { offsetHeight: 0 };
  const getComputed = () => ({ marginBottom: "0px" });
  assert.equal(rowHeightFromElement(el, getComputed), 0);
});

test("rowHeightFromElement is null-safe and DOM-free fallback", () => {
  assert.equal(rowHeightFromElement(null), null);
  // No window and no injected reader -> null, never throws.
  assert.equal(rowHeightFromElement({ offsetHeight: 110 }), null);
});