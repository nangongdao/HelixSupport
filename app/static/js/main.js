/**
 * Helix Support — frontend module entry (Phase 26.1/26.2/26.3)
 *
 * Loaded from index.html as `<script type="module">` alongside the legacy
 * app.js. It wires the design-token theme switcher, resolves the UI locale,
 * and exposes the modular helpers (api/i18n/state/sse/format) on a shared
 * namespace for tests and incremental migration. It does not duplicate the
 * legacy render functions, so the running UI behaviour is unchanged.
 */

import * as adminReport from "./admin-report.js?v=1.3.9";
import * as api from "./api.js?v=1.3.9";
import * as attachments from "./attachment.js?v=1.3.9";
import * as broadcast from "./broadcast.js?v=1.3.9";
import * as commands from "./commands.js?v=1.3.9";
import * as composer from "./composer.js?v=1.3.9";
import * as density from "./density.js?v=1.3.9";
import * as format from "./format.js?v=1.3.9";
import * as i18n from "./i18n.js?v=1.3.9";
import * as inspector from "./inspector.js?v=1.3.9";
import * as knowledge from "./knowledge.js?v=1.3.9";
import * as nav from "./nav.js?v=1.3.9";
import * as queueView from "./queue-view.js?v=1.3.9";
import * as qualityCharts from "./quality-charts.js?v=1.3.9";
import * as qualityPanel from "./quality-panel.js?v=1.3.9";
import * as session from "./session.js?v=1.3.9";
import * as sse from "./sse.js?v=1.3.9";
import * as state from "./state.js?v=1.3.9";
import * as ticketView from "./ticket-view.js?v=1.3.9";
import * as vqueue from "./vqueue.js?v=1.3.9";

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
    density,
    format,
    i18n,
    inspector,
    knowledge,
    nav,
    queueView,
    qualityCharts,
    qualityPanel,
    session,
    sse,
    state,
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

export default { initModules, resolveInitialTheme, applyTheme, toggleTheme, bindThemeToggle };
