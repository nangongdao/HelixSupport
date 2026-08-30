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

// ── Mobile queue drawer (app.js <500 campaign slice 19) ──

import {
  configure as configureQueueView,
  isQueueDrawerMode,
  setBackgroundInert,
  openQueueDrawer,
  closeQueueDrawer,
  bindQueueDrawer,
} from "../../app/static/js/queue-view.js";

function fakePane() {
  return {
    className: "queue-pane",
    hidden: false,
    listeners: {},
    attrs: {},
    classList: {
      contains: (name) => name === "is-open" && drawerOpen,
      add: (name) => { if (name === "is-open") drawerOpen = true; },
      remove: (name) => { if (name === "is-open") drawerOpen = false; },
    },
    setAttribute(name, value) { this.attrs[name] = value; },
    removeAttribute(name) { delete this.attrs[name]; },
    addEventListener(name, handler) { this.listeners[name] = handler; },
    removeEventListener(name) { delete this.listeners[name]; },
    parentElement: { insertBefore(node) { scrimNode = node; } },
  };
}

let drawerOpen = false;
let scrimNode = null;
const inertTargets = [];

function installDrawerWindow({ drawerMode }) {
  const cleanupFns = [];
  const pane = fakePane();
  const els = {
    queuePane: pane,
    mobileQueue: {
      attrs: {},
      listeners: {},
      setAttribute(name, value) { this.attrs[name] = value; },
      addEventListener(name, handler) { this.listeners[name] = handler; },
      focus() {},
    },
    backToQueue: { listeners: {}, addEventListener(name, handler) { this.listeners[name] = handler; } },
  };
  const documentStub = {
    activeElement: null,
    getElementById: () => null,
    querySelector: (selector) => {
      if (selector === ".conversation-pane") {
        return {
          attrs: {},
          setAttribute(name, value) { this.attrs[name] = value; inertTargets.push({ name, value }); },
          removeAttribute(name) { this.attrs[name] = null; inertTargets.push({ name, value: null }); },
        };
      }
      return null;
    },
    createElement: () => ({ type: "", className: "", attrs: {}, hidden: false, setAttribute(n, v) { this.attrs[n] = v; }, addEventListener() {} }),
    addEventListener(name, handler) { cleanupFns.push({ name, handler }); },
  };
  const windowStub = new (class extends EventTarget {})();
  windowStub.matchMedia = (query) => ({ matches: query.includes("900px") ? drawerMode : false });
  globalThis.window = windowStub;
  globalThis.document = documentStub;
  configureQueueView({ state: {}, els, canOperate: () => true, isCompactDensity: () => false });
  return { els, pane, cleanupFns, inertTargets };
}

test("isQueueDrawerMode mirrors the 900px media query", () => {
  installDrawerWindow({ drawerMode: true });
  assert.equal(isQueueDrawerMode(), true);
  installDrawerWindow({ drawerMode: false });
  assert.equal(isQueueDrawerMode(), false);
});

test("open/close toggles the pane class, scrim and dialog role in drawer mode", () => {
  const { pane } = installDrawerWindow({ drawerMode: true });
  openQueueDrawer();
  assert.equal(drawerOpen, true);
  assert.equal(pane.attrs.role, "dialog");
  assert.equal(pane.attrs["aria-modal"], "true");
  assert.ok(scrimNode, "scrim created");
  assert.equal(scrimNode.hidden, false);
  closeQueueDrawer({ restoreFocus: false });
  assert.equal(drawerOpen, false);
  assert.equal(pane.attrs.role, undefined);
  assert.equal(scrimNode.hidden, true);
});

test("setBackgroundInert toggles the inert attribute on the conversation pane", () => {
  installDrawerWindow({ drawerMode: true });
  setBackgroundInert(true);
  assert.deepEqual(inertTargets.at(-1), { name: "inert", value: "" });
  setBackgroundInert(false);
  assert.deepEqual(inertTargets.at(-1), { name: "inert", value: null });
});

test("bindQueueDrawer wires the toggle and Escape handlers exactly once", () => {
  const { els, cleanupFns } = installDrawerWindow({ drawerMode: true });
  assert.equal(bindQueueDrawer(), true);
  assert.ok(els.mobileQueue.listeners.click);
  assert.ok(els.backToQueue.listeners.click);
  const escape = cleanupFns.find((fn) => fn.name === "keydown");
  assert.ok(escape, "document Escape handler bound");
  assert.equal(bindQueueDrawer(), true); // idempotent rebind allowed, handlers re-set
});
