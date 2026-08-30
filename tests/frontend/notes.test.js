// Helix Support — note composer & mention suggest unit tests (slice 19)
// Run: node --test tests/frontend/notes.test.js

import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";

import {
  applyMentionFromSuggest,
  bindNotes,
  configure,
  hideMentionSuggest,
  loadCollaborators,
  renderMentionSuggest,
} from "../../app/static/js/notes.js";

function stubEls() {
  const el = () => ({
    value: "",
    textContent: "",
    innerHTML: "",
    hidden: true,
    selectionStart: null,
    dataset: {},
    options: [],
    listeners: {},
    addEventListener(name, handler) {
      this.listeners[name] = handler;
    },
    querySelectorAll: () => [],
    querySelector: () => null,
    closest: () => null,
    classList: { toggle() {} },
    setSelectionRange() {},
    scrollIntoView() {},
    focus() {},
    insertAdjacentHTML() {},
  });
  return {
    noteInput: el(),
    noteForm: el(),
    mentionSuggest: el(),
  };
}

function configureDeps({ collaborators = [], me = { actor_id: "demo.admin" } } = {}) {
  const els = stubEls();
  const calls = [];
  configure({
    state: {
      selectedId: "conv-1",
      me,
      collaborators,
      mentionOpen: false,
      mentionToken: "",
      collaboratorsLoadedAt: 0,
    },
    els,
    api: async (url) => {
      calls.push(url);
      return [{ actor_id: "colleague.a", role: "operator" }];
    },
    canOperate: () => true,
    escapeHtml: (v) => String(v ?? ""),
    roleLabels: { operator: "坐席" },
    setFormBusy: (form, busy) => calls.push({ busy }),
  });
  return { calls, els };
}

beforeEach(() => {
  delete globalThis.window;
});

test("loadCollaborators fetches the roster and republishes in island mode", async () => {
  const windowStub = new (class extends EventTarget {})();
  windowStub.__HELIX_ISLAND_MODE__ = true;
  windowStub.HelixModules = { inspector: { publishInspectorState: () => { windowStub.published = true; } } };
  globalThis.window = windowStub;
  const { calls } = configureDeps();
  await loadCollaborators();
  assert.match(calls[0], /\/api\/collaborators$/);
  assert.equal(windowStub.published, true);
});

test("renderMentionSuggest filters the roster, excludes the writer and labels roles", () => {
  installWindowStub();
  const { els } = configureDeps({
    collaborators: [
      { actor_id: "demo.admin", role: "admin" },
      { actor_id: "colleague.a", role: "operator" },
    ],
  });
  renderMentionSuggest("col");
  assert.equal(els.mentionSuggest.hidden, false);
  assert.match(els.mentionSuggest.innerHTML, /colleague\.a/);
  assert.match(els.mentionSuggest.innerHTML, /坐席/);
  assert.doesNotMatch(els.mentionSuggest.innerHTML, /demo\.admin/);
});

function installWindowStub() {
  const stub = new (class extends EventTarget {})();
  globalThis.window = stub;
  return stub;
}

test("hideMentionSuggest closes the listbox and resets the token", () => {
  installWindowStub();
  const { els } = configureDeps({
    collaborators: [{ actor_id: "colleague.a", role: "operator" }],
    me: { actor_id: "demo.admin" },
  });
  renderMentionSuggest("");
  hideMentionSuggest();
  assert.equal(els.mentionSuggest.hidden, true);
  assert.equal(els.mentionSuggest.innerHTML, "");
});

test("applyMentionFromSuggest replaces the trailing token at the caret", () => {
  installWindowStub();
  const { els } = configureDeps({
    collaborators: [{ actor_id: "colleague.a", role: "operator" }],
  });
  els.noteInput.value = "请 @co 查看进展";
  els.noteInput.selectionStart = 5;
  applyMentionFromSuggest("colleague.a");
  assert.equal(els.noteInput.value, "请 @colleague.a  查看进展");
  assert.equal(els.mentionSuggest.hidden, true);
});

test("bindNotes wires the note submit through inspector.submitNote", async () => {
  const windowStub = installWindowStub();
  const posted = [];
  windowStub.HelixModules = {
    inspector: {
      submitNote: async ({ content }) => {
        posted.push(content);
        return true;
      },
    },
  };
  const { els, calls } = configureDeps();
  assert.equal(bindNotes(), true);
  els.noteInput.value = "  备注内容  ";
  els.noteForm.listeners.submit({ preventDefault() {} });
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(posted, ["备注内容"]);
  assert.equal(els.noteInput.value, "");
});
