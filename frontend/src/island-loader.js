/**
 * Helix Support — island loader (D2)
 *
 * Bridges the Vite-produced React island bundles into the legacy
 * zero-build host page. Each island is a dynamic import of its chunk
 * (content-hashed by Vite) that calls createRoot on a reserved <div>.
 *
 * The manifest is generated at build time by Vite's `manifest` option
 * (see vite.config.js) and fetched at runtime so the host page can resolve
 * chunk paths without hardcoding hashes. Without a manifest (dist not
 * built) every island is skipped — the legacy zero-build host stays the
 * only renderer and no cross-origin script load is attempted.
 */

/**
 * @typedef {{ name: string, mountId: string, yieldsLegacy?: string[] }} IslandConfig
 * @type {IslandConfig[]}
 * Each entry maps an island name (chunk filename) to the DOM element id
 * it mounts into. The mount element must already exist in index.html.
 *
 * `yieldsLegacy` lists the legacy element ids the island takes over from.
 * Once the island mounts (desktop shell only), those legacy containers are
 * hidden so the domain renders exactly once — the React island — instead of
 * a parallel legacy tree duplicating it. In a plain browser tab the islands
 * never mount, so the legacy containers stay visible and own the surface.
 * Omit `yieldsLegacy` for islands with no legacy sibling (e.g. terminal).
 */
export const ISLANDS = [
  { name: "quality", mountId: "qualityReactIsland", yieldsLegacy: ["qualityViewBuckets"] },
  {
    name: "knowledge",
    mountId: "knowledgeReactIsland",
    // The knowledge island owns the whole surface: summary + filter toolbar +
    // article list + the draft editor. Writes still bridge back to legacy
    // (helix-knowledge-save / -action) so api()/toast/reload stay in one place.
    yieldsLegacy: [
      "knowledgeSummary",
      "knowledgeSearch",
      "knowledgeStatusFilter",
      "knowledgeLanguageFilter",
      "knowledgeResultCount",
      "knowledgeReadOnly",
      "knowledgeList",
      "knowledgeListStatus",
      "knowledgeEditor",
    ],
  },
  // The ticket island owns the status filter + list; the legacy detail view
  // (#ticketDetailView) stays legacy until a later D3 slice migrates it.
  { name: "ticket", mountId: "ticketReactIsland", yieldsLegacy: ["ticketStatusFilter", "ticketList"] },
  // The admin island owns the whole #adminContent card grid (quota,
  // members, webhooks, report subscriptions/export, CSAT, SLA, routing).
  // Writes bridge back to legacy (helix-admin-*) so api()/toast/confirm
  // stay in one place; queries stay disabled until helix-identity reports
  // admin:manage, so a non-admin island never issues privileged requests.
  {
    name: "admin",
    mountId: "adminReactIsland",
    yieldsLegacy: [
      "adminQuotaCard",
      "adminMembersCard",
      "adminWebhooksCard",
      "adminReportSubsCard",
      "adminReportExportCard",
      "adminCsatCard",
      "adminSlaCard",
      "adminRoutingCard",
    ],
  },
  // The settings island owns the settings surface: the desktop runtime
  // readout tracks window.__HELIX_BACKEND__ + helix-backend-ready (the
  // async equivalent of legacy loadDesktopInfo's re-run on view switch).
  {
    name: "settings",
    mountId: "settingsReactIsland",
    yieldsLegacy: ["settingsDesktopCard", "settingsPrefsCard"],
  },
  // The queue island owns the conversation list AND the footer strip
  // (count + load-more); the bulk toolbar and the mentions badge stay
  // legacy (the badge has its own session.js lifecycle).
  { name: "queue", mountId: "queueReactIsland", yieldsLegacy: ["conversationList", "queueCount", "loadMore"] },
  // The dashboard island owns the workspace metrics strip; legacy
  // foreground refreshAll cycles drive it via helix-dashboard-refresh
  // (background polls never refetched the dashboard, and still don't).
  { name: "dashboard", mountId: "dashboardReactIsland", yieldsLegacy: ["metrics"] },
  // The identity island owns the header readout (actor · role) as a pure
  // helix-identity subscriber; the surrounding header toggles stay legacy.
  { name: "identity", mountId: "identityReactIsland", yieldsLegacy: ["operatorIdentity"] },
  { name: "inspector", mountId: "inspectorReactIsland", yieldsLegacy: ["inspectorTabs", "inspectorOverview", "inspectorEvidence", "inspectorAudit"] },
  // The composer island owns the message forms in the desktop shell; the
  // legacy draft/macro/copilot lifecycle stays legacy via event bridges.
  { name: "composer", mountId: "composerReactIsland", yieldsLegacy: ["customerForm", "operatorForm"] },
  // The command-palette island owns the Ctrl+K palette; the legacy
  // #commandPalette dialog would otherwise double-handle the shortcut.
  { name: "command-palette", mountId: "commandPaletteReactIsland", yieldsLegacy: ["commandPalette"] },
  { name: "session-shell", mountId: "sessionShellReactIsland" },
  { name: "terminal", mountId: "terminalReactIsland" },
];

/**
 * Hide the legacy containers an island takes over from. Pure DOM mutation,
 * exported for unit testing. Returns the ids that were actually hidden (the
 * element existed and was not already hidden).
 * @param {string[]} ids - legacy element ids to yield
 * @param {Document} [doc] - document (injectable for tests)
 * @returns {string[]}
 */
export function yieldLegacyContainers(ids, doc = document) {
  const hidden = [];
  for (const id of ids || []) {
    const el = doc.getElementById(id);
    if (el && !el.hidden) {
      el.hidden = true;
      hidden.push(id);
    }
  }
  return hidden;
}

/**
 * Resolve the URL for an island chunk from the Vite manifest. Returns null
 * when the manifest has no entry for the island — the caller then skips it
 * instead of falling back to a dev-server origin that the production CSP
 * (script-src 'self') would block with a console error.
 * @param {string} name - island chunk name
 * @param {Record<string, object>} [manifest] - Vite manifest entries
 * @returns {string|null} resolved URL or null when unavailable
 */
export function resolveIslandUrl(name, manifest) {
  // Vite's manifest keys entries by their source path (see vite.config.js
  // rollupOptions.input); the `name` here is the input key's basename.
  const entry = manifest && manifest[`src/islands/${name}-island.jsx`];
  if (entry && entry.file) {
    return `/static/dist/${entry.file}`;
  }
  return null;
}

/**
 * Fetch the Vite manifest so island chunks resolve to their content-hashed
 * filenames. Missing manifest (dist not built) resolves to null and every
 * island is skipped — the legacy host page stays fully functional.
 * @returns {Promise<Record<string, object>|null>}
 */
export async function fetchManifest() {
  try {
    const res = await fetch("/static/dist/manifest.json");
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

/**
 * Load and mount all registered islands. Called once after the host page
 * is interactive. Safe to call in a browser tab (islands are no-ops if
 * their mount element doesn't exist or no built manifest is available).
 * @param {Record<string, object>} [manifest] - Vite manifest
 */
export async function loadIslands(manifest) {
  const results = [];
  if (!manifest) return results; // dist not built — legacy-only mode
  // Islands are the desktop-shell renderer. In a plain browser tab the
  // legacy app.js owns every surface; mounting a parallel React tree on
  // the non-hidden mount points would duplicate quality/knowledge/command
  // palette/terminal. The desktop shell (main.js) opts in by setting
  // __HELIX_ISLAND_MODE__ before calling loadIslands.
  if (typeof window === "undefined" || !window.__HELIX_ISLAND_MODE__) {
    return results;
  }
  for (const island of ISLANDS) {
    const mount = document.getElementById(island.mountId);
    // A hidden mount means the legacy renderer is still the primary surface
    // for this domain (dual-track migration); mounting a parallel React
    // tree there would duplicate interactive DOM for selectors and axe.
    if (!mount || mount.hidden) continue;
    const url = resolveIslandUrl(island.name, manifest);
    if (!url) continue; // no manifest entry — skip silently
    try {
      const mod = await import(/* @vite-ignore */ url);
      if (typeof mod.mount === "function") {
        mod.mount(mount);
        // Now that the React island owns this surface, hide the legacy
        // containers it takes over from so the domain renders once.
        if (island.yieldsLegacy) {
          yieldLegacyContainers(island.yieldsLegacy);
        }
        results.push({ name: island.name, ok: true });
      }
    } catch (err) {
      console.warn(`[islands] failed to load ${island.name}:`, err);
      results.push({ name: island.name, ok: false, error: String(err) });
    }
  }
  return results;
}

export default {
  ISLANDS,
  resolveIslandUrl,
  fetchManifest,
  loadIslands,
  yieldLegacyContainers,
};
