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
 * @typedef {{ name: string, mountId: string }} IslandConfig
 * @type {IslandConfig[]}
 * Each entry maps an island name (chunk filename) to the DOM element id
 * it mounts into. The mount element must already exist in index.html.
 */
export const ISLANDS = [
  { name: "quality", mountId: "qualityReactIsland" },
  { name: "knowledge", mountId: "knowledgeReactIsland" },
  { name: "ticket", mountId: "ticketReactIsland" },
  { name: "queue", mountId: "queueReactIsland" },
  { name: "inspector", mountId: "inspectorReactIsland" },
  { name: "composer", mountId: "composerReactIsland" },
  { name: "command-palette", mountId: "commandPaletteReactIsland" },
  { name: "session-shell", mountId: "sessionShellReactIsland" },
  { name: "terminal", mountId: "terminalReactIsland" },
];

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
        results.push({ name: island.name, ok: true });
      }
    } catch (err) {
      console.warn(`[islands] failed to load ${island.name}:`, err);
      results.push({ name: island.name, ok: false, error: String(err) });
    }
  }
  return results;
}

export default { ISLANDS, resolveIslandUrl, fetchManifest, loadIslands };
