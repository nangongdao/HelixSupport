// Helix Support — queue-view module unit tests (ROADMAP §43.6)
// Run: node --test tests/frontend/queue-view.test.js

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  QUEUE_ROW_PARTS,
  createQueueViewState,
  reduceQueueView,
} from "../../app/static/js/queue-view.js";

const WINDOW = { first: 0, last: 49, count: 50, topPad: 0, bottomPad: 9500 };

test("QUEUE_ROW_PARTS pins the stable class names a row is built from", () => {
  assert.deepEqual(Object.keys(QUEUE_ROW_PARTS).sort(), [
    "ITEM",
    "PAD",
    "ROW",
    "SELECT",
  ]);
  assert.equal(QUEUE_ROW_PARTS.ROW, "conversation-row");
  assert.equal(QUEUE_ROW_PARTS.SELECT, "conversation-checkbox");
});

test("createQueueViewState starts in full mode with no window", () => {
  const state = createQueueViewState();
  assert.equal(state.virtual, false);
  assert.equal(state.window, null);
  assert.equal(state.windowSig, "");
  assert.equal(state.rowHeight, 0);
  assert.equal(state.measuring, false);
});

test("reduceQueueView set-conversations enters virtual mode once", () => {
  let state = createQueueViewState();
  const next = reduceQueueView(state, { type: "set-conversations", virtual: true });
  assert.equal(next.virtual, true);
  // Re-entering virtual is a no-op and returns the same reference.
  assert.equal(reduceQueueView(next, { type: "set-conversations", virtual: true }), next);
});

test("reduceQueueView full render resets the window when leaving virtual", () => {
  let state = reduceQueueView(createQueueViewState(), {
    type: "set-conversations",
    virtual: true,
  });
  state = reduceQueueView(state, { type: "window-rendered", window: WINDOW, windowSig: "a|b" });
  assert.equal(state.window, WINDOW);
  assert.equal(state.windowSig, "a|b");
  // Full-mode render clears the band + signature.
  const exited = reduceQueueView(state, { type: "set-conversations", virtual: false });
  assert.equal(exited.virtual, false);
  assert.equal(exited.window, null);
  assert.equal(exited.windowSig, "");
});

test("reduceQueueView exit-virtual is idempotent on an already-full state", () => {
  const state = createQueueViewState();
  assert.equal(reduceQueueView(state, { type: "exit-virtual" }), state);
});

test("reduceQueueView measure-row records live geometry and measuring flag", () => {
  let state = reduceQueueView(createQueueViewState(), {
    type: "measure-row",
    rowHeight: 118,
    measuring: true,
  });
  assert.equal(state.rowHeight, 118);
  assert.equal(state.measuring, true);
  state = reduceQueueView(state, { type: "measure-row", rowHeight: 122, measuring: false });
  assert.equal(state.rowHeight, 122);
  assert.equal(state.measuring, false);
});

test("reduceQueueView unknown actions return the same reference", () => {
  const state = createQueueViewState();
  assert.equal(reduceQueueView(state, { type: "nope" }), state);
});
