/**
 * Helix Support — nav DOM lifecycle (app.js <500 campaign).
 *
 * The global navigation rail's DOM behaviour, extracted verbatim from app.js
 * (§17.1): setNavActive toggles the is-active rail state, showAppView shows/
 * hides the top-level view mount points (and loads the D1 settings readout),
 * switchAppView routes a nav selection onto its view with the quality/admin/
 * knowledge loaders, currentAppView reads the active rail item.
 *
 * js/nav.js remains the pure view registry (NAV_VIEWS/isNavView/...); this
 * module owns the DOM side and arrives its loaders through configure because
 * they are app.js-scoped wrappers.
 */

let ctx = null;

/** Inject the legacy app.js singletons (els, loaders, scheduleIdle). */
function configure(deps) {
  ctx = deps;
}

function setNavActive(view) {
  for (const item of ctx.els.navItems) {
    item.classList.toggle("is-active", item.dataset.view === view);
  }
}

function showAppView(name) {
  const views = {
    workspace: ctx.els.workspaceView,
    quality: ctx.els.qualityView,
    knowledge: ctx.els.knowledgeView,
    admin: ctx.els.adminView,
  };
  for (const [key, element] of Object.entries(views)) {
    if (element) element.hidden = key !== name;
  }
  // D1 设置页：真实的桌面运行时信息（版本/端口/数据目录），不再是占位文案。
  if (ctx.els.placeholderView) {
    ctx.els.placeholderView.hidden = name !== "settings";
    if (ctx.els.placeholderView.hidden === false) ctx.loadDesktopInfo();
  }
}

function switchAppView(view) {
  setNavActive(view);
  showAppView(view);
  if (view === "quality") {
    // Island mode: the quality island owns the buckets (yieldsLegacy) and
    // fetches via react-query — hand the refresh over instead of fetching
    // into the hidden legacy containers (unforced refresh reuses the
    // island's 10s throttle; the header refresh button sends force).
    if (typeof window !== "undefined" && window.__HELIX_ISLAND_MODE__) {
      window.dispatchEvent(new CustomEvent("helix-quality-refresh", { detail: { force: false } }));
    } else {
      // Reuse the Phase 21 aggregates; the parametrized renderer fills the
      // standalone view containers. The fresh fetch is non-critical — the
      // cached buckets render immediately, so it is scheduled for idle time.
      ctx.renderQualityPanel(ctx.els.qualityViewBuckets, ctx.els.qualityViewGaps);
      ctx.scheduleIdle(() => ctx.loadQualityPanel());
    }
  }
  if (view === "admin") {
    void ctx.loadAdminView();
  }
  if (view === "knowledge") {
    void ctx.loadKnowledgeView();
  }
}

function currentAppView() {
  const active = ctx.els.navItems.find((item) => item.classList.contains("is-active"));
  return active ? active.dataset.view : "workspace";
}

export { configure, setNavActive, showAppView, switchAppView, currentAppView };

export default { configure, setNavActive, showAppView, switchAppView, currentAppView };