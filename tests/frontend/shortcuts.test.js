// Helix Support — global keyboard shortcuts unit tests (app.js <500 slice 24)
// Run: node --test tests/frontend/shortcuts.test.js

import { beforeEach, test } from "node:test";
import assert from "node:assert/strict";

import { bindShortcuts, configure } from "../../app/static/js/shortcuts.js";

/** Collect keydown handlers the way the module installs them. */
function installDocument() {
  const handlers = [];
  globalThis.document = {
    addEventListener: (type, handler) => {
      if (type === "keydown") handlers.push(handler);
    },
  };
  return {
    handlers,
    press(key, extra = {}) {
      let prevented = false;
      const event = { key, preventDefault: () => { prevented = true; }, ...extra };
      handlers.forEach((handler) => handler(event));
      return prevented;
    },
  };
}

/** The module guards typing with `instanceof` against the real DOM classes. */
function installElementClasses() {
  class HTMLInputElement {}
  class HTMLTextAreaElement {}
  class HTMLSelectElement {}
  Object.assign(globalThis, { HTMLInputElement, HTMLTextAreaElement, HTMLSelectElement });
  return { HTMLInputElement, HTMLTextAreaElement, HTMLSelectElement };
}

function configureDeps({ state = {} } = {}) {
  const clicked = [];
  const focused = [];
  const calls = [];
  const els = {
    searchInput: { focus: () => focused.push("searchInput") },
    newConversation: { click: () => clicked.push("newConversation") },
    inspectorToggle: { click: () => clicked.push("inspectorToggle") },
    lowPerfToggle: { click: () => clicked.push("lowPerfToggle") },
  };
  const model = {
    conversations: [{ id: "c1" }, { id: "c2" }, { id: "c3" }],
    selectedId: "c1",
    ...state,
  };
  configure({
    state: model,
    els,
    refreshAll: () => calls.push("refresh"),
    // The real selectConversation moves the selection; j/k read it back, so
    // the stub has to advance it too or every press restarts from the top.
    selectConversation: (id) => {
      model.selectedId = id;
      calls.push({ select: id });
    },
  });
  return { clicked, focused, calls, els };
}

beforeEach(() => {
  delete globalThis.document;
});

test("bindShortcuts installs a keydown handler on the document", () => {
  const doc = installDocument();
  installElementClasses();
  configureDeps();
  assert.equal(bindShortcuts(), true);
  assert.equal(doc.handlers.length, 1);
});

test("single keys drive search focus, the dialog, refresh and toggles", () => {
  const doc = installDocument();
  installElementClasses();
  const { clicked, focused, calls } = configureDeps();
  bindShortcuts();

  assert.equal(doc.press("/"), true, "the browser's quick-find is suppressed");
  assert.deepEqual(focused, ["searchInput"]);

  doc.press("c");
  assert.deepEqual(clicked, ["newConversation"]);

  doc.press("r");
  assert.deepEqual(calls, ["refresh"]);

  doc.press("i");
  doc.press("l");
  assert.deepEqual(clicked, ["newConversation", "inspectorToggle", "lowPerfToggle"]);
});

test("j and k walk the queue with clamping at both ends", () => {
  const doc = installDocument();
  installElementClasses();
  const { calls } = configureDeps();
  bindShortcuts();

  doc.press("j"); // c1 -> c2
  assert.deepEqual(calls, [{ select: "c2" }]);
  doc.press("j"); // c2 -> c3
  doc.press("j"); // clamped
  assert.deepEqual(calls.at(-1), { select: "c3" });
  doc.press("k"); // c3 -> c2
  assert.deepEqual(calls.at(-1), { select: "c2" });
  doc.press("k"); // c2 -> c1
  doc.press("k"); // clamped
  assert.deepEqual(calls.at(-1), { select: "c1" });
});

test("an unselected conversation starts j/k from the first row", () => {
  const doc = installDocument();
  installElementClasses();
  const { calls } = configureDeps({ state: { selectedId: null } });
  bindShortcuts();
  doc.press("j");
  assert.deepEqual(calls, [{ select: "c2" }], "index -1 is clamped to 0, then +1");
});

test("typing in a form control is never hijacked", () => {
  const doc = installDocument();
  const { HTMLInputElement, HTMLTextAreaElement, HTMLSelectElement } = installElementClasses();
  const { calls, clicked, focused } = configureDeps();
  bindShortcuts();

  for (const target of [
    new HTMLInputElement(),
    new HTMLTextAreaElement(),
    new HTMLSelectElement(),
  ]) {
    assert.equal(doc.press("/", { target }), false, "preventDefault is skipped while editing");
  }
  assert.deepEqual({ calls, clicked, focused }, { calls: [], clicked: [], focused: [] });
});

test("modifier combinations stay with the browser (Ctrl+K included)", () => {
  const doc = installDocument();
  installElementClasses();
  const { calls, clicked, focused } = configureDeps();
  bindShortcuts();

  doc.press("k", { ctrlKey: true });
  doc.press("r", { metaKey: true });
  doc.press("l", { altKey: true });
  assert.deepEqual({ calls, clicked, focused }, { calls: [], clicked: [], focused: [] });
});

test("j/k are inert on an empty queue", () => {
  const doc = installDocument();
  installElementClasses();
  const { calls } = configureDeps({ state: { conversations: [] } });
  bindShortcuts();
  doc.press("j");
  assert.deepEqual(calls, []);
});
