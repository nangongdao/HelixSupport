// Helix Support — density module unit tests (UI 升级 §17.2 三档密度)
// Run: node --test tests/frontend/density.test.js

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  DENSITY_LEVELS,
  DEFAULT_DENSITY,
  normalizeDensity,
  nextDensity,
  effectiveDensity,
  isCompactDensity,
} from "../../app/static/js/density.js";

test("DENSITY_LEVELS is the three-level cycle in order", () => {
  assert.deepEqual(DENSITY_LEVELS, ["comfortable", "compact", "dense"]);
  assert.equal(DEFAULT_DENSITY, "comfortable");
});

test("normalizeDensity falls back to comfortable for junk", () => {
  assert.equal(normalizeDensity("compact"), "compact");
  assert.equal(normalizeDensity("dense"), "dense");
  assert.equal(normalizeDensity(null), "comfortable");
  assert.equal(normalizeDensity(""), "comfortable");
  assert.equal(normalizeDensity("ultra"), "comfortable");
});

test("nextDensity cycles through the three levels", () => {
  assert.equal(nextDensity("comfortable"), "compact");
  assert.equal(nextDensity("compact"), "dense");
  assert.equal(nextDensity("dense"), "comfortable");
  assert.equal(nextDensity("bogus"), "compact");
});

test("effectiveDensity forces compact in low-perf without losing the choice", () => {
  assert.equal(effectiveDensity("dense", true), "compact");
  assert.equal(effectiveDensity("comfortable", true), "compact");
  assert.equal(effectiveDensity("dense", false), "dense");
  assert.equal(effectiveDensity("compact", false), "compact");
});

test("isCompactDensity treats compact and dense as compact layouts", () => {
  assert.equal(isCompactDensity("compact", false), true);
  assert.equal(isCompactDensity("dense", false), true);
  assert.equal(isCompactDensity("comfortable", false), false);
  assert.equal(isCompactDensity("dense", true), true);
});
