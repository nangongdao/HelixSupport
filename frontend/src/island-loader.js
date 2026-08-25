/**
 * Helix Support — island loader (D2)
 *
 * Bridges the Vite-produced React island bundles into the legacy
 * zero-build host page. Each island is a dynamic import of its chunk
 * (content-hashed by Vite) that calls createRoot on a reserved <div>.
 *
 * The manifest is generated at build time by Vite's `manifest` option
 * (see vite.config.js) so the host page can resolve chunk paths without
 * hardcoding hashes. In dev mode the loader falls back to the Vite dev
 * server URL.
 */

const DEV_ORIGIN = "http://127.0.0.1:5173";

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
 * Resolve the URL for an island chunk, using the Vite manifest in prod
 * or the dev server in dev.
 * @param {string} name - island chunk name
 * @param {Record<string, string>} [manifest] - Vite manifest entries
 * @returns {string} resolved URL
 */
export function resolveIslandUrl(name, manifest) {
  if (manifest && manifest[`${name}.js`]) {
    return `/static/dist/${manifest[`${name}.js`].file}`;
  }
  // Dev server (no manifest): /static/dist/ is proxied by the backend in dev
  // or served directly by Vite at DEV_ORIGIN.
  if (typeof window !== "undefined" && "__TAURI_INTERNALS__" in window) {
    return `/static/dist/${name}.js`;
  }
  return `${DEV_ORIGIN}/src/islands/${name}-island.jsx`;
}

/**
 * Load and mount all registered islands. Called once after the host page
 * is interactive. Safe to call in a browser tab (islands are no-ops if
 * their mount element doesn't exist).
 * @param {Record<string, string>} [manifest] - Vite manifest
 */
export async function loadIslands(manifest) {
  const results = [];
  for (const island of ISLANDS) {
    const mount = document.getElementById(island.mountId);
    if (!mount) continue; // mount point not on this page
    try {
      const url = resolveIslandUrl(island.name, manifest);
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

export default { ISLANDS, resolveIslandUrl, loadIslands };
