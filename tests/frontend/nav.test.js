// Helix Support — global navigation module unit tests (UI 升级 §17.1)
// Run: node --test tests/frontend/nav.test.js

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  NAV_VIEWS,
  NAV_PLACEHOLDER_VIEWS,
  isNavView,
  isPlaceholderView,
} from "../../app/static/js/nav.js";

test("NAV_VIEWS lists the five top-level mount points", () => {
  assert.deepEqual(NAV_VIEWS, ["workspace", "quality", "knowledge", "admin", "settings"]);
});

test("isNavView accepts every registered view", () => {
  for (const view of NAV_VIEWS) assert.equal(isNavView(view), true);
  assert.equal(isNavView("bogus"), false);
  assert.equal(isNavView(""), false);
});

test("isPlaceholderView marks settings only", () => {
  assert.deepEqual(NAV_PLACEHOLDER_VIEWS, ["settings"]);
  for (const view of NAV_PLACEHOLDER_VIEWS) assert.equal(isPlaceholderView(view), true);
  assert.equal(isPlaceholderView("workspace"), false);
  assert.equal(isPlaceholderView("quality"), false);
  assert.equal(isPlaceholderView("admin"), false); // 17.3 admin page is real
});

test("real views are workspace, quality, knowledge, and admin", () => {
  for (const view of ["workspace", "quality", "knowledge", "admin"]) {
    assert.equal(isPlaceholderView(view), false, `${view} must not be a placeholder`);
  }
});
