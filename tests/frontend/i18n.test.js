// Helix Support — i18n module unit tests (Phase 26.3)
// Run: node --test tests/frontend/i18n.test.js

import { test } from "node:test";
import assert from "node:assert/strict";

import { LOCALES, resolveLocale, t, missingKeys, packFor } from "../../app/static/js/i18n.js";

test("resolveLocale defaults to zh-CN when nothing matches", () => {
  assert.equal(resolveLocale(null), "zh-CN");
  assert.equal(resolveLocale(""), "zh-CN");
  assert.equal(resolveLocale("fr-FR,fr;q=0.9"), "zh-CN");
});

test("resolveLocale prefers en from Accept-Language", () => {
  assert.equal(resolveLocale("en-US,en;q=0.9"), "en");
  assert.equal(resolveLocale("en"), "en");
  assert.equal(resolveLocale("en-GB"), "en");
});

test("resolveLocale respects a stored preference", () => {
  assert.equal(resolveLocale("zh-CN", "en"), "en");
  assert.equal(resolveLocale("en-US", "zh-CN"), "zh-CN");
});

test("resolveLocale ignores invalid stored preference", () => {
  assert.equal(resolveLocale("en-US", "bogus"), "en");
});

test("t returns the zh-CN translation for known keys", () => {
  assert.equal(t("status.open"), "自动处理中");
  assert.equal(t("action.save"), "保存");
  assert.equal(t("status.resolved"), "已解决");
});

test("t falls back to the key when unknown", () => {
  assert.equal(t("no.such.key"), "no.such.key");
});

test("t supports an explicit locale", () => {
  assert.equal(t("status.open", "en"), "status.open");
});

test("t interpolates {name} placeholders from params", () => {
  assert.equal(t("density.toggle", { level: "紧凑" }), "密度:紧凑（点击切换）");
  assert.equal(t("density.toggle", { level: "舒适" }), "密度:舒适（点击切换）");
});

test("t interpolation leaves unknown placeholders untouched", () => {
  assert.equal(t("density.toggle", {}), "密度:{level}（点击切换）");
});

test("missingKeys is empty for the zh-CN pack itself", () => {
  assert.equal(missingKeys().length, 0);
});

test("en pack defines every zh-CN key", () => {
  const missing = missingKeys();
  assert.deepEqual(missing, []);
});

test("packFor returns zh-CN for unknown locales", () => {
  assert.equal(packFor("xx"), packFor("zh-CN"));
});

test("LOCALES lists zh-CN and en", () => {
  assert.deepEqual(Object.keys(LOCALES).sort(), ["en", "zh-CN"]);
});
