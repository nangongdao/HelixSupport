/**
 * Helix Support — desktop-info + view glue module (extracted from app.js)
 *
 * Holds the settings-page desktop runtime info (D1) and the knowledge/admin
 * view-loading glue that dispatches island events. Extracted to bring the
 * legacy app.js controller under the §D3 <500-line target.
 *
 * Loaded from app.js via the HelixModules namespace.
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
    if (els.desktopBackendPort)
      els.desktopBackendPort.textContent = backend.port
        ? `127.0.0.1:${backend.port}`
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

/**
 * Load knowledge articles and dispatch a helix-knowledge-loaded island event.
 * @param {Object} els
 * @param {Object} deps - { api, dispatchIslandEvent, showToast }
 */
export async function loadKnowledgeView(els, deps) {
  if (!els.knowledgeView) return;
  const { api, dispatchIslandEvent, showToast } = deps;
  try {
    const articles = await api("/api/knowledge");
    const list = Array.isArray(articles) ? articles : articles?.items || [];
    dispatchIslandEvent("helix-knowledge-loaded", { articles: list });
  } catch (e) {
    showToast(e.message, true);
  }
}

/**
 * Load admin view data (quota/members/webhooks) and dispatch a
 * helix-admin-loaded island event. Requires admin/platform role.
 * @param {Object} els
 * @param {Object} deps - { api, dispatchIslandEvent, showToast, state, canManage }
 */
export async function loadAdminView(els, deps) {
  if (!els.adminView || !deps.canManage()) return;
  const { api, dispatchIslandEvent, showToast, state } = deps;
  try {
    const tenantId = state.me?.tenant_id || "demo";
    const [quota, members, webhooks] = await Promise.all([
      api(`/api/admin/tenants/${encodeURIComponent(tenantId)}/quota`),
      api(`/api/admin/tenants/${encodeURIComponent(tenantId)}/members`),
      api("/api/webhooks"),
    ]);
    dispatchIslandEvent("helix-admin-loaded", { quota, members, webhooks });
  } catch (e) {
    showToast(e.message, true);
  }
}

export default { loadDesktopInfo, knowledgeFilters, loadKnowledgeView, loadAdminView };
