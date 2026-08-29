// Helix Support — island loader unit tests (ROADMAP §43.6 / DESKTOP_TAURI_PLAN §D3)
// Run: node --test tests/frontend/island-loader.test.js
//
// Covers the pure helpers (resolveIslandUrl, yieldLegacyContainers) and the
// dual-track gating invariants (no manifest → no mount, no island mode → no
// mount, hidden mount → skipped). loadIslands' dynamic import is exercised
// against a stubbed global loader module so no network/Vite build is needed.

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  ISLANDS,
  resolveIslandUrl,
  yieldLegacyContainers,
} from "../../frontend/src/island-loader.js";

// ── resolveIslandUrl ────────────────────────────────────────────────────

test("resolveIslandUrl reads the Vite manifest entry keyed by source path", () => {
  const manifest = {
    "src/islands/quality-island.jsx": { file: "quality-Ab12.js" },
  };
  assert.equal(resolveIslandUrl("quality", manifest), "/static/dist/quality-Ab12.js");
});

test("resolveIslandUrl returns null when the island has no manifest entry", () => {
  assert.equal(resolveIslandUrl("quality", {}), null);
  assert.equal(resolveIslandUrl("quality", undefined), null);
  assert.equal(resolveIslandUrl("quality", { "src/islands/quality-island.jsx": {} }), null);
});

// ── yieldLegacyContainers ───────────────────────────────────────────────

function fakeDoc(ids) {
  const map = new Map();
  for (const id of ids) map.set(id, { id, hidden: false });
  return {
    getElementById: (id) => map.get(id) || null,
  };
}

test("yieldLegacyContainers hides the listed legacy containers", () => {
  const doc = fakeDoc(["qualityViewBuckets", "qualityViewGaps"]);
  const hidden = yieldLegacyContainers(["qualityViewBuckets"], doc);
  assert.deepEqual(hidden, ["qualityViewBuckets"]);
  assert.equal(doc.getElementById("qualityViewBuckets").hidden, true);
  assert.equal(doc.getElementById("qualityViewGaps").hidden, false);
});

test("yieldLegacyContainers skips missing and already-hidden elements", () => {
  const doc = fakeDoc(["knowledgeList"]);
  doc.getElementById("knowledgeList").hidden = true; // already hidden
  const hidden = yieldLegacyContainers(
    ["knowledgeList", "knowledgeSummary", "knowledgeReadOnly"],
    doc,
  );
  assert.deepEqual(hidden, []);
});

test("yieldLegacyContainers tolerates an empty/undefined list", () => {
  assert.deepEqual(yieldLegacyContainers([], fakeDoc([])), []);
  assert.deepEqual(yieldLegacyContainers(undefined, fakeDoc([])), []);
});

// ── ISLANDS registry ────────────────────────────────────────────────────

test("ISLANDS entries that take over a legacy surface declare yieldsLegacy", () => {
  const byName = Object.fromEntries(ISLANDS.map((i) => [i.name, i]));
  // quality owns the trend buckets but not the gaps (island renders no gaps).
  assert.deepEqual(byName.quality.yieldsLegacy, ["qualityViewBuckets"]);
  // knowledge owns the whole surface: summary + toolbar + list + editor.
  assert.ok(byName.knowledge.yieldsLegacy.includes("knowledgeList"));
  assert.ok(byName.knowledge.yieldsLegacy.includes("knowledgeEditor"));
  // command palette owns the Ctrl+K dialog.
  assert.deepEqual(byName["command-palette"].yieldsLegacy, ["commandPalette"]);
  // ticket owns the status filter + list; the detail view stays legacy.
  assert.deepEqual(byName.ticket.yieldsLegacy, ["ticketStatusFilter", "ticketList"]);
  assert.ok(!byName.ticket.yieldsLegacy.includes("ticketDetailView"));
  // queue owns the conversation list, the footer strip (count + load-more)
  // and the bulk toolbar; only the mentions badge stays legacy.
  assert.deepEqual(byName.queue.yieldsLegacy, [
    "conversationList",
    "queueCount",
    "loadMore",
    "bulkToolbar",
  ]);
  // dashboard owns the workspace metrics strip; the refresh cadence stays
  // legacy via helix-dashboard-refresh on foreground cycles.
  assert.deepEqual(byName.dashboard.yieldsLegacy, ["metrics"]);
  // identity owns the header readout; the header toggles stay legacy.
  assert.deepEqual(byName.identity.yieldsLegacy, ["operatorIdentity"]);
  // conversation dialog owns the new-conversation <dialog>; the create
  // lifecycle stays legacy via helix-conversation-create/-created.
  assert.deepEqual(byName["conversation-dialog"].yieldsLegacy, ["newConversationDialog"]);
  // workspace tabs owns the 队列/工单 tablist; pane switching stays legacy.
  assert.deepEqual(byName["workspace-tabs"].yieldsLegacy, ["workspaceTabs"]);
  // admin owns the whole card grid: quota/members/webhooks/reports/CSAT/
  // SLA/routing; the denial panel (#adminDenied) and header stay legacy.
  assert.deepEqual(byName.admin.yieldsLegacy, [
    "adminQuotaCard",
    "adminMembersCard",
    "adminWebhooksCard",
    "adminReportSubsCard",
    "adminReportExportCard",
    "adminCsatCard",
    "adminSlaCard",
    "adminRoutingCard",
  ]);
  // settings owns both cards (desktop runtime readout + preferences); the
  // heading stays legacy.
  assert.deepEqual(byName.settings.yieldsLegacy, ["settingsDesktopCard", "settingsPrefsCard"]);
  // composer owns the message forms; the send lifecycle stays legacy via bridges.
  assert.deepEqual(byName.composer.yieldsLegacy, ["customerForm", "operatorForm"]);
  // inspector owns the tabs + detail panels; the quality self-fetch stays legacy.
  assert.deepEqual(byName.inspector.yieldsLegacy, [
    "inspectorTabs",
    "inspectorOverview",
    "inspectorEvidence",
    "inspectorAudit",
  ]);
  // terminal has no legacy sibling.
  assert.equal(byName.terminal.yieldsLegacy, undefined);
});

test("every ISLANDS entry has a name and a mountId", () => {
  for (const island of ISLANDS) {
    assert.ok(typeof island.name === "string" && island.name.length > 0);
    assert.ok(typeof island.mountId === "string" && island.mountId.length > 0);
  }
});

// ── loadIslands gating (no-network) ─────────────────────────────────────
//
// loadIslands dynamic-imports the chunk URL, which we cannot reach without a
// Vite build. We stub the global loader module the host imports and assert
// the gating branches (no manifest, no island mode, hidden mount) return
// before any import is attempted.

async function loadIslandsViaStub({ manifest, islandMode, mounts }) {
  // Re-import the loader fresh with a controlled globals shim.
  const loaderUrl = new URL("../../frontend/src/island-loader.js", import.meta.url);
  // Inject window/document stubs before the module reads them at call time.
  const fakeWindow = islandMode ? { __HELIX_ISLAND_MODE__: true } : {};
  const fakeDocument = {
    getElementById: (id) => mounts?.[id] || null,
  };
  const mod = await import(loaderUrl);
  const origWindow = globalThis.window;
  const origDocument = globalThis.document;
  globalThis.window = fakeWindow;
  globalThis.document = fakeDocument;
  try {
    return await mod.loadIslands(manifest);
  } finally {
    globalThis.window = origWindow;
    globalThis.document = origDocument;
  }
}

test("loadIslands is a no-op without a manifest (dist not built)", async () => {
  const results = await loadIslandsViaStub({
    manifest: null,
    islandMode: true,
    mounts: { qualityReactIsland: { hidden: false } },
  });
  assert.deepEqual(results, []);
});

test("loadIslands is a no-op outside the desktop shell (no island mode)", async () => {
  const manifest = { "src/islands/quality-island.jsx": { file: "quality-Ab12.js" } };
  const results = await loadIslandsViaStub({
    manifest,
    islandMode: false,
    mounts: { qualityReactIsland: { hidden: false } },
  });
  assert.deepEqual(results, []);
});

test("loadIslands skips islands whose mount point is hidden", async () => {
  const manifest = { "src/islands/session-shell-island.jsx": { file: "shell-Ab12.js" } };
  const results = await loadIslandsViaStub({
    manifest,
    islandMode: true,
    mounts: { sessionShellReactIsland: { hidden: true } },
  });
  assert.deepEqual(results, []);
});
