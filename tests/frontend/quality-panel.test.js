// Helix Support — quality panel unit tests (D3 long tail glue slice)
// Run: node --test tests/frontend/quality-panel.test.js
//
// Covers the two render paths of js/quality-panel.js:
// - the pure panel-HTML builders (shared by the legacy innerHTML path and
//   the island publish path, so both render identical markup), and
// - the island branch of loadQualityPanel, which publishes the built HTML
//   to the inspector island via helix-inspector-quality instead of writing
//   into the hidden legacy containers.

import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";

import {
  INSPECTOR_QUALITY_EVENT,
  bindQuality,
  buildQualityBucketsHtml,
  buildQualityGapsHtml,
  configure,
  loadQualityPanel,
} from "../../app/static/js/quality-panel.js";
import { escapeHtml } from "../../app/static/js/format.js";

const BUCKETS = [
  {
    date: "2026-08-29",
    intent: "退款",
    prompt_version: 3,
    turn_count: 6,
    escalation_count: 1,
    negative_feedback_count: 0,
    estimated_tokens: 120,
    avg_latency_ms: 820.5,
  },
];

const GAPS = [
  {
    conversation_id: "conv-1",
    message_id: "msg-1",
    customer_name: "客户甲",
    intent: "退款",
    assistant_content: "抱歉给您带来不便……",
  },
];

/** Minimal window stub: quality-panel.js only needs an event target plus
 * the island-mode flag (HelixModules.qualityCharts stays unset, so the
 * builders render without charts — same as the browser before that module
 * registers). */
function installWindow({ islandMode }) {
  const target = new EventTarget();
  target.__HELIX_ISLAND_MODE__ = islandMode;
  globalThis.window = target;
  return target;
}

function configureDeps({ api, showToast }) {
  configure({
    state: { me: { permissions: ["metrics:read"] }, qualityLoadedAt: 0, qualityBuckets: [], qualityGaps: [] },
    els: {},
    api,
    showToast,
    escapeHtml,
  });
}

/** Collect helix-inspector-quality payloads published on the window stub. */
function collectQualityEvents(windowStub) {
  const events = [];
  windowStub.addEventListener(INSPECTOR_QUALITY_EVENT, (event) => {
    events.push(event.detail);
  });
  return events;
}

beforeEach(() => {
  delete globalThis.window;
});

test("buildQualityBucketsHtml renders the empty state with no buckets", () => {
  installWindow({ islandMode: false });
  configureDeps({ api: async () => [], showToast: async () => {} });
  const html = buildQualityBucketsHtml([]);
  assert.match(html, /暂无质量数据/);
  assert.match(html, /quality-empty/);
});

test("buildQualityBucketsHtml renders one card per intent with escaped labels", () => {
  installWindow({ islandMode: false });
  configureDeps({ api: async () => [], showToast: async () => {} });
  const html = buildQualityBucketsHtml(BUCKETS);
  assert.match(html, /quality-card/);
  assert.match(html, /退款/);
  assert.match(html, /16\.7%/); // escalation rate 1/6
  assert.match(html, /821 ms/); // rounded avg latency
  const evil = [{ ...BUCKETS[0], intent: '<script>alert(1)</script>' }];
  assert.doesNotMatch(buildQualityBucketsHtml(evil), /<script>/);
});

test("buildQualityGapsHtml renders draft buttons with data attributes", () => {
  installWindow({ islandMode: false });
  configureDeps({ api: async () => [], showToast: async () => {} });
  const html = buildQualityGapsHtml(GAPS);
  assert.match(html, /quality-gap-draft/);
  assert.match(html, /data-conversation-id="conv-1"/);
  assert.match(html, /data-message-id="msg-1"/);
  assert.equal(buildQualityGapsHtml([]).includes("quality-gap-draft"), false);
});

test("island-mode loadQualityPanel publishes the built panel HTML to the inspector island", async () => {
  const windowStub = installWindow({ islandMode: true });
  let apiCalls = 0;
  configureDeps({
    api: async (url) => {
      apiCalls += 1;
      return url.includes("knowledge-gaps") ? GAPS : BUCKETS;
    },
    showToast: async () => {},
  });
  const events = collectQualityEvents(windowStub);
  await loadQualityPanel();
  assert.equal(apiCalls, 2);
  assert.equal(events.length, 1);
  assert.match(events[0].bucketsHtml, /quality-card/);
  assert.match(events[0].gapsHtml, /quality-gap-draft/);
});

test("island-mode loadQualityPanel serves a re-open from the 10s cache without refetching", async () => {
  const windowStub = installWindow({ islandMode: true });
  let apiCalls = 0;
  configureDeps({
    api: async () => {
      apiCalls += 1;
      return GAPS;
    },
    showToast: async () => {},
  });
  const events = collectQualityEvents(windowStub);
  await loadQualityPanel();
  const afterFirst = apiCalls;
  await loadQualityPanel(); // within the throttle window
  assert.equal(apiCalls, afterFirst);
  assert.equal(events.length, 2);
  assert.match(events[1].bucketsHtml, /quality-empty|quality-card/);
});

test("island-mode loadQualityPanel reports a permission denial without fetching", async () => {
  const windowStub = installWindow({ islandMode: true });
  let apiCalls = 0;
  configure({
    state: { me: { permissions: [] }, qualityLoadedAt: 0, qualityBuckets: [], qualityGaps: [] },
    els: {},
    api: async () => { apiCalls += 1; return []; },
    showToast: async () => {},
    escapeHtml,
  });
  const events = collectQualityEvents(windowStub);
  await loadQualityPanel();
  assert.equal(apiCalls, 0);
  assert.equal(events.length, 1);
  assert.match(events[0].bucketsHtml, /当前角色无权限查看质量看板/);
});

test("island-mode loadQualityPanel reports a fetch failure as the buckets message", async () => {
  const windowStub = installWindow({ islandMode: true });
  configureDeps({
    api: async () => {
      throw new Error("boom");
    },
    showToast: async () => {},
  });
  const events = collectQualityEvents(windowStub);
  await loadQualityPanel();
  assert.equal(events.length, 1);
  assert.match(events[0].bucketsHtml, /质量数据加载失败/);
});

test("bindQuality bridges helix-quality-draft to the knowledge-draft write", async () => {
  const windowStub = installWindow({ islandMode: false });
  const calls = [];
  configureDeps({
    api: async (url, options) => {
      calls.push({ url, options });
      return { title: "草稿" };
    },
    showToast: async (message) => {
      calls.push({ toast: message });
    },
  });
  assert.equal(bindQuality(), true);
  windowStub.dispatchEvent(
    new CustomEvent("helix-quality-draft", {
      detail: { conversationId: "conv-9", messageId: "msg-9" },
    }),
  );
  await new Promise((resolve) => setTimeout(resolve, 0));
  const post = calls.find((call) => call.url);
  assert.ok(post, "expected the knowledge-draft POST");
  assert.match(post.url, /\/api\/conversations\/conv-9\/messages\/msg-9\/knowledge-draft/);
});
