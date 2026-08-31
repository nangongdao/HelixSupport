// Helix Support — admin report/SLA/routing island bridge unit tests (app.js <500 sl24)
// Run: node --test tests/frontend/admin-report-bridge.test.js

import { afterEach, test } from "node:test";
import assert from "node:assert/strict";

import {
  configure,
  createRuleFromIsland,
  createSubscriptionFromIsland,
  deleteRuleFromIsland,
  generateReportFromIsland,
  saveSlaFromIsland,
  toggleSubscriptionFromIsland,
} from "../../app/static/js/admin-report-bridge.js";

/** Capture window.dispatchEvent so bridges' receipts can be asserted. */
function installWindow() {
  const events = [];
  globalThis.window = { dispatchEvent: (event) => events.push(event) };
  return events;
}
afterEach(() => {
  delete globalThis.window;
});

function configureDeps({ apiImpl } = {}) {
  const calls = [];
  const toasts = [];
  configure({
    api: apiImpl || (async (path, options) => {
      calls.push({ path, options });
      return { rows: [], from_date: "2026-01-01", to_date: "2026-01-07" };
    }),
    showToast: (msg, isError) => toasts.push({ msg, isError }),
  });
  return { calls, toasts };
}

function savedEvents(events) {
  return events.filter((e) => e.type === "helix-admin-saved").map((e) => e.detail);
}

test("createSubscription requires an endpoint and posts the full payload", async () => {
  const events = installWindow();
  const { toasts } = configureDeps();

  await createSubscriptionFromIsland({ reportType: "usage", schedule: "weekly", windowDays: 14 });
  assert.equal(toasts[0].msg, "请先选择 Webhook 端点");
  assert.equal(savedEvents(events).length, 0, "no write, no refetch receipt");

  const { calls } = configureDeps();
  await createSubscriptionFromIsland({ reportType: "usage", schedule: "weekly", windowDays: 14, webhookEndpointId: "w1" });
  const body = JSON.parse(calls[0].options.body);
  assert.deepEqual(body, {
    report_type: "usage",
    schedule: "weekly",
    window_days: 14,
    webhook_endpoint_id: "w1",
  });
  assert.deepEqual(savedEvents(events).at(-1), { ok: true, domains: ["subscriptions"] });
});

test("generateReport emits the verbatim preview with the Chinese label", async () => {
  const events = installWindow();
  configureDeps({
    apiImpl: async () => ({
      rows: [{ k: "v" }],
      from_date: "2026-01-01",
      to_date: "2026-01-07",
    }),
  });
  await generateReportFromIsland({ reportType: "quality" });
  const [reportEvent] = events.filter((e) => e.type === "helix-admin-report-generated");
  assert.equal(reportEvent.detail.ok, true);
  assert.ok(
    reportEvent.detail.text.startsWith("质量报表 2026-01-01 → 2026-01-07：1 行"),
    `preview text got: ${reportEvent.detail.text}`,
  );
});

test("generateReport failure answers ok:false", async () => {
  const events = installWindow();
  configureDeps({ apiImpl: async () => { throw new Error("boom"); } });
  await generateReportFromIsland({});
  const [reportEvent] = events.filter((e) => e.type === "helix-admin-report-generated");
  assert.equal(reportEvent.detail.ok, false);
});

test("saveSla validates both SLA minutes", async () => {
  const events = installWindow();
  const { calls, toasts } = configureDeps();

  await saveSlaFromIsland({ priority: "high", firstResponseMinutes: 5 });
  assert.equal(toasts[0].msg, "请填写首响与解决时限");
  assert.equal(calls.length, 0);

  await saveSlaFromIsland({ priority: "high", channel: " email ", firstResponseMinutes: 5, resolveMinutes: 60 });
  assert.deepEqual(JSON.parse(calls[0].options.body), {
    priority: "high",
    channel: "email",
    first_response_minutes: 5,
    resolve_minutes: 60,
  });
  assert.deepEqual(savedEvents(events).at(-1), { ok: true, domains: ["sla"] });
});

test("createRule trims optional fields and skips empties", async () => {
  const events = installWindow();
  const { calls, toasts } = configureDeps();

  await createRuleFromIsland({ intent: "refund" });
  assert.equal(toasts[0].msg, "请先选择分配组");

  await createRuleFromIsland({ intent: " refund ", label: " ", channel: "web", groupId: "g1", priority: 2 });
  const body = JSON.parse(calls[0].options.body);
  assert.deepEqual(body, { group_id: "g1", priority: 2, intent: "refund", channel: "web" });
  assert.deepEqual(savedEvents(events).at(-1), { ok: true, domains: ["rules"] });
});

test("toggle and delete subscriptions/rule guard empty ids and report refetch", async () => {
  const events = installWindow();
  const { calls } = configureDeps();

  await toggleSubscriptionFromIsland({});
  assert.equal(calls.length, 0, "missing id is a no-op");

  await toggleSubscriptionFromIsland({ id: "s1", active: false });
  assert.equal(calls[0].path, "/api/admin/report-subscriptions/s1");
  assert.deepEqual(JSON.parse(calls[0].options.body), { active: false });
  assert.deepEqual(savedEvents(events).at(-1), { ok: true, domains: ["subscriptions"] });

  await deleteRuleFromIsland({});
  assert.equal(calls.length, 1, "missing rule id is a no-op");

  await deleteRuleFromIsland({ id: "r1" });
  assert.equal(calls[1].path, "/api/admin/routing-rules/r1");
  assert.deepEqual(savedEvents(events).at(-1), { ok: true, domains: ["rules"] });
});