// Helix Support — format/util module unit tests (Phase 26.1)
// Run: node --test tests/frontend/format.test.js

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  escapeHtml,
  statusLabel,
  roleLabel,
  formatTime,
  newIdempotencyKey,
  nextCursor,
  backoffDelay,
} from "../../app/static/js/format.js";

test("escapeHtml escapes the five HTML metacharacters", () => {
  assert.equal(escapeHtml(`<a href="x">&'`), "&lt;a href=&quot;x&quot;&gt;&amp;&#039;");
});

test("escapeHtml handles null and undefined", () => {
  assert.equal(escapeHtml(null), "");
  assert.equal(escapeHtml(undefined), "");
});

test("escapeHtml passes through plain text", () => {
  assert.equal(escapeHtml("hello 世界"), "hello 世界");
});

test("statusLabel maps known statuses in zh-CN", () => {
  assert.equal(statusLabel("open"), "自动处理中");
  assert.equal(statusLabel("waiting_human"), "等待人工");
  assert.equal(statusLabel("human_active"), "人工处理中");
  assert.equal(statusLabel("resolved"), "已解决");
});

test("statusLabel returns the raw value for unknown statuses", () => {
  assert.equal(statusLabel("weird"), "weird");
});

test("statusLabel supports en locale", () => {
  assert.equal(statusLabel("open", "en"), "auto");
});

test("roleLabel maps known roles", () => {
  assert.equal(roleLabel("admin"), "管理员");
  assert.equal(roleLabel("auditor"), "审计员");
  assert.equal(roleLabel("operator", "en"), "operator");
});

test("roleLabel returns the raw value for unknown roles", () => {
  assert.equal(roleLabel("robot"), "robot");
});

test("formatTime returns dash for empty", () => {
  assert.equal(formatTime(""), "-");
  assert.equal(formatTime(null), "-");
});

test("formatTime returns the raw value for invalid dates", () => {
  assert.equal(formatTime("not-a-date"), "not-a-date");
});

test("formatTime formats a valid ISO timestamp", () => {
  const out = formatTime("2026-01-02T03:04:00Z");
  assert.match(out, /\d{2}:\d{2}/);
});

test("newIdempotencyKey returns a 16-hex-char key", () => {
  const key = newIdempotencyKey();
  assert.match(key, /^[0-9a-f]{16}$/);
});

test("newIdempotencyKey is effectively unique", () => {
  const a = newIdempotencyKey();
  const b = newIdempotencyKey();
  assert.notEqual(a, b);
});

test("nextCursor reads a header object", () => {
  assert.equal(nextCursor({ "X-Next-Cursor": "abc" }), "abc");
  assert.equal(nextCursor({}), null);
});

test("nextCursor reads a Headers instance", () => {
  const headers = new Headers({ "X-Next-Cursor": "xyz" });
  assert.equal(nextCursor(headers), "xyz");
});

test("nextCursor returns null for empty input", () => {
  assert.equal(nextCursor(null), null);
  assert.equal(nextCursor(undefined), null);
});

test("backoffDelay grows exponentially with jitter", () => {
  // Deterministic RNG: always returns 0.5 -> jitter factor 0.75.
  const rng = () => 0.5;
  assert.equal(backoffDelay(0, 1000, 30000, rng), 750);
  assert.equal(backoffDelay(1, 1000, 30000, rng), 1500);
  assert.equal(backoffDelay(2, 1000, 30000, rng), 3000);
});

test("backoffDelay caps at max", () => {
  const rng = () => 1.0;
  assert.equal(backoffDelay(20, 1000, 5000, rng), 5000);
});

test("backoffDelay defaults use Math.random and sane bounds", () => {
  const delay = backoffDelay(0, 1000, 30000);
  assert.ok(delay >= 500 && delay <= 30000, `delay ${delay} out of range`);
});
