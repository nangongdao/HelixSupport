// Helix Support — quality dashboard SVG charts unit tests (ROADMAP §17.3)
// Run: node --test tests/frontend/quality-charts.test.js

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  escapeXml,
  qualityTrendSvg,
  intentVersionHeatmapSvg,
  heatmapColor,
} from "../../app/static/js/quality-charts.js";

const ROWS = [
  { date: "2026-08-15", intent: "refund", prompt_version: "v1", turn_count: 10 },
  { date: "2026-08-16", intent: "refund", prompt_version: "v1", turn_count: 20 },
  { date: "2026-08-15", intent: "shipping", prompt_version: "v2", turn_count: 5 },
  { date: "2026-08-16", intent: "shipping", prompt_version: "v2", turn_count: 15 },
];

test("escapeXml escapes markup-breaking characters", () => {
  assert.equal(escapeXml("<script>alert(1)</script>"), "&lt;script&gt;alert(1)&lt;/script&gt;");
  assert.equal(escapeXml('a"b&c'), "a&quot;b&amp;c");
  assert.equal(escapeXml(null), "");
});

test("qualityTrendSvg aggregates per day and draws a polyline", () => {
  const svg = qualityTrendSvg(ROWS);
  assert.ok(svg.startsWith("<svg"), "returns svg markup");
  assert.match(svg, /<polyline/);
  // refund+shipping summed per date: 15 on 08-15, 35 on 08-16.
  assert.match(svg, /2026-08-15: 15/);
  assert.match(svg, /2026-08-16: 35/);
  assert.ok(!svg.includes("<script>"), "no raw markup");
  const byDay = (svg.match(/<circle/g) || []).length;
  assert.equal(byDay, 2, "one dot per day");
});

test("qualityTrendSvg returns empty string for fewer than two days", () => {
  assert.equal(qualityTrendSvg([]), "");
  assert.equal(qualityTrendSvg([{ date: "2026-08-15", turn_count: 10 }]), "");
  assert.equal(qualityTrendSvg(null), "");
});

test("qualityTrendSvg honours a custom metric", () => {
  const rows = [
    { date: "2026-08-15", escalation_count: 3 },
    { date: "2026-08-16", escalation_count: 7 },
  ];
  const svg = qualityTrendSvg(rows, { metric: "escalation_count" });
  assert.match(svg, /<polyline/);
  assert.match(svg, /2026-08-16: 7/);
  // A metric absent from every row still draws a flat zero line (the window
  // genuinely had none of that metric) rather than failing.
  const zero = qualityTrendSvg(rows, { metric: "missing_field" });
  assert.match(zero, /<polyline/);
});

test("qualityTrendSvg escapes date labels", () => {
  const rows = [
    { date: "<2026-08-15>", turn_count: 1 },
    { date: "<2026-08-16>", turn_count: 2 },
  ];
  const svg = qualityTrendSvg(rows);
  assert.ok(!svg.includes("<2026-08-15>"), "raw date never reaches the markup");
  assert.ok(svg.includes("&lt;2026-08-15&gt;"));
});

test("intentVersionHeatmapSvg builds an intent x version matrix", () => {
  const svg = intentVersionHeatmapSvg(ROWS);
  assert.ok(svg.startsWith("<svg"), "returns svg markup");
  assert.equal((svg.match(/class="[^"]*\bqc-row\b[^"]*"/g) || []).length, 2, "two intent rows");
  assert.equal((svg.match(/class="[^"]*\bqc-head\b[^"]*"/g) || []).length, 2, "two version columns");
  // The matrix covers every intent × version pair: refund×v1 and shipping×v2
  // are populated, the other two pairs have zero volume (empty cells).
  assert.equal((svg.match(/class="qc-cell"/g) || []).length, 2, "populated cells");
  assert.equal((svg.match(/qc-cell-empty/g) || []).length, 2, "zero-volume cells");
  // Totals aggregate across dates: refund×v1 = 10+20 = 30, shipping×v2 = 5+15 = 20.
  assert.match(svg, />30</);
  assert.match(svg, />20</);
});

test("intentVersionHeatmapSvg returns empty string without data", () => {
  assert.equal(intentVersionHeatmapSvg([]), "");
  assert.equal(intentVersionHeatmapSvg([{ date: "2026-08-15", turn_count: 9 }]), "");
  assert.equal(intentVersionHeatmapSvg(null), "");
});

test("intentVersionHeatmapSvg escapes intent and version labels", () => {
  const rows = [
    { intent: "<refund>", prompt_version: "v<1>", turn_count: 5 },
  ];
  const svg = intentVersionHeatmapSvg(rows);
  assert.ok(!svg.includes("<refund>"));
  assert.ok(!svg.includes("v<1>"));
  assert.ok(svg.includes("&lt;refund&gt;"));
  assert.ok(svg.includes("v&lt;1&gt;"));
});

test("intentVersionHeatmapSvg leaves zero-volume cells unfilled", () => {
  const rows = [
    { intent: "a", prompt_version: "v1", turn_count: 8 },
    { intent: "b", prompt_version: "v1", turn_count: 0 },
  ];
  const svg = intentVersionHeatmapSvg(rows);
  assert.equal((svg.match(/qc-cell-empty/g) || []).length, 1, "zero cell marked empty");
  const filled = (svg.match(/class="qc-cell"/g) || []).length;
  assert.equal(filled, 1, "only the non-zero cell is shaded");
});

test("heatmapColor bounds: zero -> no fill, max -> strongest alpha", () => {
  assert.equal(heatmapColor(0, 10), "");
  const mid = heatmapColor(5, 10);
  assert.match(mid, /^rgba\(45, 212, 191, /);
  const midAlpha = Number(mid.match(/rgba\(45, 212, 191, ([\d.]+)\)/)[1]);
  const maxAlpha = Number(heatmapColor(10, 10).match(/rgba\(45, 212, 191, ([\d.]+)\)/)[1]);
  assert.ok(midAlpha > 0.1 && midAlpha < maxAlpha);
  assert.ok(maxAlpha <= 1);
});