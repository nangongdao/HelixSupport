// Helix Support — summary banner model unit tests (D3 long tail slice 15)
// Run: node --test tests/frontend/summary.test.js

import { afterEach, test } from "node:test";
import assert from "node:assert/strict";

import { configure, renderSummaries, SUMMARY_EVENT, summaryModel } from "../../app/static/js/summary.js";

test("SUMMARY_EVENT is the island bridge event name", () => {
  assert.equal(SUMMARY_EVENT, "helix-summary-state");
});

test("summaryModel hides the banner with no summaries", () => {
  assert.deepEqual(summaryModel([]), { visible: false, title: "", text: "" });
  assert.deepEqual(summaryModel(undefined), { visible: false, title: "", text: "" });
});

test("summaryModel renders a context summary with the projected badge", () => {
  const model = summaryModel([
    { kind: "context", source: "rule", content: "客户此前咨询过配送时效。" },
  ]);
  assert.equal(model.visible, true);
  assert.equal(model.title, "前情摘要（接入参考）");
  assert.equal(model.text, "客户此前咨询过配送时效。（自动投影）");
});

test("summaryModel marks model-sourced summaries as AI drafts", () => {
  const model = summaryModel([{ kind: "context", source: "model", content: "摘要正文" }]);
  assert.equal(model.text, "摘要正文（AI 生成草稿）");
});

test("summaryModel renders a disposition summary with its own title", () => {
  const model = summaryModel([{ kind: "disposition", source: "rule", content: "已退款并安抚客户" }]);
  assert.equal(model.title, "处置记录草稿");
  assert.equal(model.visible, true);
});

test("summaryModel prefers the context summary when both kinds exist", () => {
  const model = summaryModel([
    { kind: "disposition", source: "rule", content: "处置内容" },
    { kind: "context", source: "rule", content: "前情内容" },
  ]);
  assert.equal(model.title, "前情摘要（接入参考）");
  assert.equal(model.text, "前情内容（自动投影）");
});

test("summaryModel stays hidden when neither known kind is present", () => {
  const model = summaryModel([{ kind: "other", source: "rule", content: "无关" }]);
  assert.equal(model.visible, false);
});

// --- renderSummaries: legacy banner paint vs island model publish ---------

function installWindow({ island = false } = {}) {
  const events = [];
  globalThis.window = {
    __HELIX_ISLAND_MODE__: island,
    dispatchEvent: (event) => events.push(event),
  };
  return events;
}
function bannerEls() {
  return {
    summaryBanner: { hidden: true },
    summaryTitle: { textContent: "" },
    summaryText: { textContent: "" },
  };
}
afterEach(() => {
  delete globalThis.window;
});

test("renderSummaries paints the legacy banner from the single model source", () => {
  installWindow({ island: false });
  const els = bannerEls();
  configure({ els });

  renderSummaries({ summaries: [] });
  assert.equal(els.summaryBanner.hidden, true, "no summary keeps the banner hidden");

  renderSummaries({ summaries: [{ kind: "context", source: "rule", content: "前情" }] });
  assert.equal(els.summaryBanner.hidden, false);
  assert.equal(els.summaryTitle.textContent, "前情摘要（接入参考）");
  assert.equal(els.summaryText.textContent, "前情（自动投影）");
});

test("renderSummaries publishes the model event in island mode", () => {
  const events = installWindow({ island: true });
  configure({ els: bannerEls() });

  renderSummaries({ summaries: [{ kind: "disposition", source: "model", content: "已退款" }] });
  const [event] = events.filter((e) => e.type === SUMMARY_EVENT);
  assert.equal(event.detail.visible, true);
  assert.equal(event.detail.title, "处置记录草稿");
});
