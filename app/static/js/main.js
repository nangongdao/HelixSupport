/**
 * Helix Support — frontend module entry (Phase 26.1/26.2/26.3)
 *
 * Loaded from index.html as `<script type="module">` alongside the legacy
 * app.js. It wires the design-token theme switcher, resolves the UI locale,
 * and exposes the modular helpers (api/i18n/state/sse/format) on a shared
 * namespace for tests and incremental migration. It does not duplicate the
 * legacy render functions, so the running UI behaviour is unchanged.
 */

import * as adminReport from "./admin-report.js?v=1.4.0";
import * as api from "./api.js?v=1.4.0";
import * as attachments from "./attachment.js?v=1.4.0";
import * as broadcast from "./broadcast.js?v=1.4.0";
import * as commands from "./commands.js?v=1.4.0";
import * as composer from "./composer.js?v=1.4.0";
import * as conversationActions from "./conversation-actions.js?v=1.4.0";
import * as composerIslandBridge from "./composer-island-bridge.js?v=1.4.0";
import * as density from "./density.js?v=1.4.0";
import * as desktopInfo from "./desktop-info.js?v=1.4.0";
import * as drafts from "./drafts.js?v=1.4.0";
import * as format from "./format.js?v=1.4.0";
import * as i18n from "./i18n.js?v=1.4.0";
import * as inspector from "./inspector.js?v=1.4.0";
import * as knowledge from "./knowledge.js?v=1.4.0";
import * as knowledgeView from "./knowledge-view.js?v=1.4.0";
import * as notes from "./notes.js?v=1.4.0";
import * as savedViews from "./saved-views.js?v=1.4.0";
import * as commandDispatch from "./command-dispatch.js?v=1.4.0";
import * as refresh from "./refresh.js?v=1.4.0";
import * as nav from "./nav.js?v=1.4.0";
import * as queueView from "./queue-view.js?v=1.4.0";
import * as qualityCharts from "./quality-charts.js?v=1.4.0";
import * as qualityPanel from "./quality-panel.js?v=1.4.0";
import * as session from "./session.js?v=1.4.0";
import * as sse from "./sse.js?v=1.4.0";
import * as state from "./state.js?v=1.4.0";
import * as summary from "./summary.js?v=1.4.0";
import * as thread from "./thread.js?v=1.4.0";
import * as ticketView from "./ticket-view.js?v=1.4.0";
import * as vqueue from "./vqueue.js?v=1.4.0";

const THEME_STORAGE_KEY = "helix-theme";

/** Resolve the initial theme: stored preference, OS preference, or dark. */
export function resolveInitialTheme() {
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    /* storage unavailable */
  }
  if (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches) {
    return "light";
  }
  return "dark";
}

/** Apply a theme by setting `data-theme` on <html>. */
export function applyTheme(theme) {
  const root = document.documentElement;
  if (theme === "light") {
    root.setAttribute("data-theme", "light");
  } else {
    root.removeAttribute("data-theme");
  }
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    /* storage unavailable */
  }
  return theme;
}

/** Toggle between light and dark. */
export function toggleTheme() {
  const current = document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
  return applyTheme(current === "light" ? "dark" : "light");
}

/** Bind the theme control next to the state it mutates, exactly once. */
export function bindThemeToggle() {
  if (typeof document === "undefined") return false;
  const toggle = document.getElementById("themeToggle");
  if (!toggle || toggle.dataset.themeBound === "true") return false;
  toggle.addEventListener("click", toggleTheme);
  toggle.dataset.themeBound = "true";
  return true;
}

function bindThemeToggleWhenReady() {
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bindThemeToggle, { once: true });
  } else {
    bindThemeToggle();
  }
}

/**
 * Initialize the module layer: theme, locale, and a window.HelixModules
 * namespace so legacy code and tests can consume the modules.
 * @returns {{theme: string, locale: string}}
 */
export function initModules() {
  const theme = applyTheme(resolveInitialTheme());
  const locale = i18n.resolveLocale(
    (navigator.language || "").split(",")[0],
    null,
  );
  window.HelixModules = {
    adminReport,
    api,
    attachments,
    broadcast,
    commands,
    composer,
    conversationActions,
    composerIslandBridge,
    density,
    desktopInfo,
    drafts,
    format,
    i18n,
    inspector,
    knowledge,
    knowledgeView,
    notes,
    savedViews,
    commandDispatch,
    refresh,
    nav,
    queueView,
    qualityCharts,
    qualityPanel,
    session,
    sse,
    state,
    summary,
    thread,
    ticketView,
    vqueue,
    theme,
    locale,
    applyTheme,
    toggleTheme,
    bindThemeToggle,
  };
  bindThemeToggleWhenReady();
  return { theme, locale };
}

if (typeof window !== "undefined") {
  initModules();
}

/* ── Desktop shell integration (D1) ───────────────────────────────────
 * In the Tauri desktop shell the Rust supervisor spawns the Python
 * sidecar in the background and dispatches a `helix-backend-ready` event
 * once /health/ready responds. We show a splash overlay until then so the
 * window paints instantly instead of flashing an empty console.
 * In a normal browser tab the backend is already up (the page itself was
 * served by it), so the splash stays hidden and the behaviour is unchanged. */
function isDesktopShell() {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

function initDesktopSplash() {
  if (typeof document === "undefined") return;
  const splash = document.getElementById("desktopSplash");
  if (!splash) return;
  const statusEl = document.getElementById("desktopSplashStatus");
  if (isDesktopShell()) {
    splash.hidden = false;
    document.documentElement.classList.add("desktop-booting");
  }
  window.addEventListener("helix-backend-ready", () => {
    splash.hidden = true;
    document.documentElement.classList.remove("desktop-booting");
    const backend = window.__HELIX_BACKEND__;
    if (backend && backend.error && statusEl) {
      statusEl.textContent = `后端启动失败：${backend.error}`;
      splash.hidden = false;
    }
    // Notify the Rust side that the UI is interactive (startup telemetry).
    if (isDesktopShell() && window.__TAURI_INTERNALS__ && window.__TAURI_INTERNALS__.invoke) {
      window.__TAURI_INTERNALS__.invoke("ui_ready").catch(() => {});
    }
  });
}

if (typeof window !== "undefined") {
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initDesktopSplash, { once: true });
  } else {
    initDesktopSplash();
  }
}

/* ── React island bootstrap (D2) ──────────────────────────────────────
 * The island loader is a zero-build ESM module that dynamically imports
 * the Vite-produced chunk for each island. Chunk URLs come from the
 * content-hashed Vite manifest (/static/dist/manifest.json), fetched at
 * runtime.
 *
 * Mounting is gated on the Tauri desktop shell: in a plain browser tab
 * the legacy app.js is the sole renderer, so islands must NOT mount —
 * even when a built dist/ happens to be present on disk (local dev who
 * ran `vite build`, or a shared static dir). Without this gate the
 * non-hidden island mount points (quality/knowledge/command-palette/
 * terminal) would render a parallel React tree on top of the legacy
 * DOM, duplicating those surfaces. The desktop shell sets
 * `__HELIX_ISLAND_MODE__` so islands know the legacy renderer has yielded;
 * here we only ensure the loader runs at all in desktop mode. */
async function initIslands() {
  if (typeof document === "undefined") return;
  if (!isDesktopShell()) return; // browser tab — legacy app.js owns all surfaces
  // Opt the desktop shell into island mode so loadIslands mounts the
  // React islands (legacy app.js yields the migrated domains in desktop).
  window.__HELIX_ISLAND_MODE__ = true;
  try {
    const loader = await import(
      /* @vite-ignore */ "/static/dist/island-loader.js"
    ).catch(() => null);
    const { loadIslands } = loader || {};
    if (typeof loadIslands !== "function") return; // dist not built — legacy-only
    let manifest = null;
    if (typeof loader.fetchManifest === "function") {
      manifest = await loader.fetchManifest();
    }
    await loadIslands(manifest);
  } catch (err) {
    console.warn("[islands] loader unavailable:", err);
  }
}

if (typeof window !== "undefined") {
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initIslands, { once: true });
  } else {
    initIslands();
  }
}


export default { initModules, resolveInitialTheme, applyTheme, toggleTheme, bindThemeToggle };
