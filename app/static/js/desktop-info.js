/**
 * Helix Support — desktop-info module (extracted from app.js)
 *
 * Holds the settings-page desktop runtime info (D1). In island mode the
 * settings island (frontend/src/islands/settings-island.jsx) owns this
 * readout and tracks helix-backend-ready itself; this legacy path stays
 * for the browser dual-track. Loaded from app.js via HelixModules.
 *
 * (The earlier loadKnowledgeView/loadAdminView island-event glue lived
 * here but dispatched events no consumer ever listened for — the real
 * bridges are the helix-knowledge / helix-admin event families in app.js
 * — and has been removed as dead code.)
 */

const DESKTOP_VERSION = "1.4.0";

/**
 * Populate the settings page with desktop runtime info (version/port/mode/
 * data dir). No-op in a browser tab (fields read "仅桌面可用").
 * @param {Object} els - element refs from app.js
 */
export function loadDesktopInfo(els) {
  const isDesktop =
    typeof window !== "undefined" &&
    (window.__HELIX_BACKEND__ !== undefined || "__TAURI_INTERNALS__" in window);
  if (els.desktopVersion) els.desktopVersion.textContent = DESKTOP_VERSION;
  if (isDesktop) {
    if (els.desktopEnvNote) els.desktopEnvNote.hidden = true;
    const backend = window.__HELIX_BACKEND__ || {};
    // The shell injects { backendPort: location.port } (src-tauri/src/lib.rs);
    // the old read of backend.port matched nothing, so the port readout sat
    // at "等待中…" since D1.
    if (els.desktopBackendPort)
      els.desktopBackendPort.textContent = backend.backendPort
        ? `127.0.0.1:${backend.backendPort}`
        : "等待中…";
    if (els.desktopBackendMode)
      els.desktopBackendMode.textContent = backend.error
        ? `错误：${backend.error}`
        : "桌面 sidecar";
    if (els.desktopDataDir) els.desktopDataDir.textContent = "%APPDATA%/HelixSupport/data";
  } else {
    if (els.desktopEnvNote) els.desktopEnvNote.hidden = false;
    if (els.desktopBackendPort) els.desktopBackendPort.textContent = "仅桌面可用";
    if (els.desktopBackendMode) els.desktopBackendMode.textContent = "浏览器环境";
    if (els.desktopDataDir) els.desktopDataDir.textContent = "仅桌面可用";
  }
}

/**
 * Read the knowledge view filter inputs.
 * @param {Object} els - element refs from app.js
 * @returns {{status:string, language:string, query:string}}
 */
export function knowledgeFilters(els) {
  return {
    status: els.knowledgeStatusFilter?.value || "all",
    language: els.knowledgeLanguageFilter?.value || "",
    query: els.knowledgeSearch?.value || "",
  };
}

export default { loadDesktopInfo, knowledgeFilters };
