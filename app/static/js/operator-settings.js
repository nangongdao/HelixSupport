/**
 * Helix Support — operator settings + permission gates (app.js <500 campaign).
 *
 * The remaining genuine (non-delegating) console helpers, extracted verbatim
 * from app.js: the low-perf/queue-size/poll-interval prefs, the permission
 * gates (canOperate/canWriteConversations/canReadConversations), latestAssistant,
 * and setDensity/applyWorkspacePreferences (the density + workspace prefs UI).
 *
 * density.js is imported directly for normalizeDensity/effectiveDensity (the
 * plain ESM module, no app.js facade indirection); state/els and the app-core
 * queue-size constants arrive through configure.
 */

import { effectiveDensity, normalizeDensity } from "./density.js?v=1.4.0";

let ctx = null;

/** Inject the legacy app.js singletons (state/els/queue-size constants). */
function configure(deps) {
  ctx = deps;
}

function queuePageSize() {
  return ctx.state.lowPerf ? ctx.QUEUE_PAGE_SIZE_LOW : ctx.QUEUE_PAGE_SIZE_NORMAL;
}

function pollInterval() {
  return ctx.state.lowPerf ? ctx.POLL_INTERVAL_LOW : ctx.POLL_INTERVAL_NORMAL;
}

function detectConstrainedDevice() {
  const cores = navigator.hardwareConcurrency || 8;
  const memory = navigator.deviceMemory || 8;
  const saveData = navigator.connection?.saveData === true;
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  return cores <= 4 || memory <= 4 || saveData || reducedMotion;
}

function applyWorkspacePreferences() {
  document.body.classList.toggle("is-low-perf", ctx.state.lowPerf);
  document.body.classList.toggle("is-inspector-collapsed", ctx.state.inspectorCollapsed);
  if (ctx.els.lowPerfToggle) {
    ctx.els.lowPerfToggle.setAttribute("aria-pressed", String(ctx.state.lowPerf));
    ctx.els.lowPerfToggle.title = ctx.state.lowPerf ? "关闭低配模式" : "开启低配模式";
    ctx.els.lowPerfToggle.setAttribute("aria-label", ctx.state.lowPerf ? "关闭低配模式" : "开启低配模式");
  }
  if (ctx.els.inspectorToggle) {
    ctx.els.inspectorToggle.setAttribute("aria-pressed", String(ctx.state.inspectorCollapsed));
    ctx.els.inspectorToggle.title = ctx.state.inspectorCollapsed ? "展开检查器" : "折叠检查器";
    ctx.els.inspectorToggle.setAttribute(
      "aria-label",
      ctx.state.inspectorCollapsed ? "展开检查器" : "折叠检查器",
    );
  }
  if (ctx.els.inspectorSurface) {
    ctx.els.inspectorSurface.hidden = ctx.state.inspectorCollapsed;
    ctx.els.inspectorSurface.setAttribute("aria-hidden", String(ctx.state.inspectorCollapsed));
  }
}

function canOperate() {
  return ctx.state.me?.permissions?.includes("operator:act") === true;
}

// Backlog (多语言客服): language override + translate both require
// conversation:write (the console's write gate, distinct from canOperate's
// operator:act so channel/operator roles can still act).
function canWriteConversations() {
  return ctx.state.me?.permissions?.includes("conversation:write") === true;
}

function canReadConversations() {
  return Boolean(ctx.state.me && ctx.state.me.permissions && ctx.state.me.permissions.includes("conversation:read"));
}

function latestAssistant(messages) {
  return [...messages].reverse().find((message) => message.role === "assistant") || null;
}

const DENSITY_LABELS = { comfortable: "舒适", compact: "紧凑", dense: "密集" };

function setDensity(level, { persist = true } = {}) {
  ctx.state.density = normalizeDensity(level);
  // Row heights change with density — force a live re-measure next render.
  ctx.state.queueRowHeight = 0;
  const effective = effectiveDensity(ctx.state.density, ctx.state.lowPerf);
  document.body.setAttribute("data-density", effective);
  if (persist) window.localStorage.setItem(ctx.PREF_DENSITY, ctx.state.density);
  if (ctx.els.densityToggle) {
    const i18n = window.HelixModules?.i18n;
    const label = DENSITY_LABELS[effective] || effective;
    const title = i18n?.t
      ? i18n.t("density.toggle", { level: label })
      : `密度:${label}（点击切换）`;
    ctx.els.densityToggle.setAttribute("aria-pressed", String(effective !== "comfortable"));
    ctx.els.densityToggle.title = title;
    ctx.els.densityToggle.setAttribute("aria-label", title);
  }
}

export {
  configure,
  queuePageSize,
  pollInterval,
  detectConstrainedDevice,
  applyWorkspacePreferences,
  canOperate,
  canWriteConversations,
  canReadConversations,
  latestAssistant,
  setDensity,
};

export default {
  configure,
  queuePageSize,
  pollInterval,
  detectConstrainedDevice,
  applyWorkspacePreferences,
  canOperate,
  canWriteConversations,
  canReadConversations,
  latestAssistant,
  setDensity,
};