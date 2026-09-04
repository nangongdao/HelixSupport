// Helix Support — shared UI/format helper unit tests (app.js <500 helpers slice)
// Run: node --test tests/frontend/helpers.test.js

import { afterEach, beforeEach, mock, test } from "node:test";
import assert from "node:assert/strict";

import {
  configure,
  escapeHtml,
  formatSla,
  formatTime,
  icon,
  newIdempotencyKey,
  roleLabel,
  scheduleIdle,
  setFormBusy,
  showToast,
  statusLabel,
} from "../../app/static/js/helpers.js";

beforeEach(() => {
  // The module reads window.* only for timers/crypto/i18n/requestIdleCallback;
  // default them so the pure tests need no further setup.
  globalThis.window = {
    setTimeout: (...args) => setTimeout(...args),
    clearTimeout: (...args) => clearTimeout(...args),
  };
});
afterEach(() => {
  mock.timers.reset();
  delete globalThis.window;
});

test("escapeHtml neutralises all five HTML metacharacters", () => {
  assert.equal(escapeHtml(`<a href="x" & 'y' >`), "&lt;a href=&quot;x&quot; &amp; &#039;y&#039; &gt;");
  assert.equal(escapeHtml(null), "");
});

test("icon builds the sprite use-tag with the current cache key", () => {
  assert.equal(
    icon("refresh"),
    '<svg class="icon" aria-hidden="true"><use href="/static/icons.svg?v=1.4.0#refresh"></use></svg>',
  );
});

test("statusLabel prefers i18n and keeps the raw code when untranslated", () => {
  globalThis.window.HelixModules = { i18n: { t: (key) => (key === "status.open" ? "已开启" : undefined) } };
  assert.equal(statusLabel("open"), "已开启");
  assert.equal(statusLabel("resolved"), "resolved", "untranslated key keeps the raw code");
  assert.equal(statusLabel(null), "未知", "a falsy status resolves to the generic label");
});

test("statusLabel uses the hardcoded labels when i18n is absent", () => {
  assert.equal(statusLabel("resolved"), "已解决");
  assert.equal(statusLabel("mystery"), "mystery", "an unknown code passes through");
  assert.equal(statusLabel(null), "未知");
});

test("roleLabel falls back to the hardcoded labels without i18n", () => {
  assert.equal(roleLabel("operator"), "客服");
  assert.equal(roleLabel("not-a-role"), "not-a-role");
});

test("formatTime renders time, optional date, and passthrough for bad input", () => {
  assert.equal(formatTime(null), "-");
  assert.equal(formatTime("not-a-date"), "not-a-date");
  const iso = "2026-01-02T10:30:00";
  const withDate = formatTime(iso, true);
  assert.match(withDate, /01\/02/);
  assert.match(withDate, /10:30/);
});

test("formatSla reports resolved / missing / breached / remaining", () => {
  assert.deepEqual(formatSla({ status: "resolved" }), { text: "已完成", breached: false });
  assert.deepEqual(formatSla({ status: "open" }), { text: "SLA -", breached: false });
  const past = new Date(Date.now() - 5 * 60000).toISOString();
  assert.equal(formatSla({ status: "open", sla_due_at: past }).breached, true);
  const soon = new Date(Date.now() + 30 * 60000).toISOString();
  assert.match(formatSla({ status: "open", sla_due_at: soon }).text, /剩余 \d+ 分钟/);
  const later = new Date(Date.now() + 3 * 3600000).toISOString();
  assert.match(formatSla({ status: "open", sla_due_at: later }).text, /剩余 \d+ 小时/);
});

test("showToast paints the toast and auto-hides after 3.6s", () => {
  mock.timers.enable({ apis: ["setTimeout"] });
  const classes = {};
  const toast = {
    textContent: "",
    hidden: true,
    classList: { toggle: (name, on) => { classes[name] = on; } },
  };
  configure({ els: { toast } });

  showToast("已保存", false);
  assert.equal(toast.textContent, "已保存");
  assert.equal(toast.hidden, false);
  assert.equal(classes["is-error"], false);

  mock.timers.tick(3600);
  assert.equal(toast.hidden, true);
});

test("scheduleIdle delegates to setTimeout when requestIdleCallback is absent", () => {
  mock.timers.enable({ apis: ["setTimeout"] });
  let ran = 0;
  scheduleIdle(() => { ran += 1; });
  assert.equal(ran, 0);
  mock.timers.tick(250);
  assert.equal(ran, 1);
});

test("scheduleIdle catches a throwing idle task so it never surfaces", () => {
  mock.timers.enable({ apis: ["setTimeout"] });
  const original = console.error;
  const logged = [];
  console.error = (...args) => { logged.push(args); };
  try {
    scheduleIdle(() => { throw new Error("idle boom"); });
    mock.timers.tick(250);
  } finally {
    console.error = original;
  }
  assert.equal(logged.length, 1, "the error is logged once, not thrown");
});

test("setFormBusy flags the form and disables every control", () => {
  const controls = [{ disabled: false }, { disabled: true }];
  const form = {
    dataset: {},
    setAttribute: (name, value) => { form.attrs = { ...(form.attrs || {}), [name]: value }; },
    querySelectorAll: () => controls,
  };
  setFormBusy(form, true);
  assert.equal(form.dataset.busy, "true");
  assert.equal(form.attrs["aria-busy"], "true");
  assert.deepEqual(controls.map((c) => c.disabled), [true, true]);
});

test("newIdempotencyKey shares the console's ui- prefix", () => {
  const key = newIdempotencyKey();
  assert.match(key, /^ui-/);
  assert.ok(key.length > 3, "a fallback key still carries entropy");
});