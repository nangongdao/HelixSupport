/**
 * Helix Support — queue filter controls (app.js <500 campaign slice 24).
 *
 * The queue filter inputs (status/label/priority/ownership/channel/sort), the
 * "needs response" focus toggle, the debounced search box and the label-filter
 * <select> renderer. Extracted from app.js; state/els, refreshAll and
 * escapeHtml arrive through configure.
 *
 * Both tracks use this module: the desktop shell yields these controls to the
 * queue island, but the island still asks the legacy layer to reload, so the
 * listeners stay the single source of the refresh contract.
 */

let ctx = null;
let searchTimer = null;

/** Inject the legacy app.js singletons (state/els/refreshAll/escapeHtml). */
export function configure(deps) {
  ctx = deps;
}

/** Rebuild the label filter options, keeping a selection the catalog lost. */
export function renderLabelFilter() {
  const { els, state, escapeHtml } = ctx;
  const selected = els.labelFilter.value;
  const options = state.labelCatalog.map(
    (item) => `<option value="${escapeHtml(item.label)}">${escapeHtml(item.label)} · ${escapeHtml(item.conversation_count)}</option>`,
  );
  if (selected && !state.labelCatalog.some((item) => item.label === selected)) {
    options.unshift(`<option value="${escapeHtml(selected)}">${escapeHtml(selected)}</option>`);
  }
  els.labelFilter.innerHTML = `<option value="">全部标签</option>${options.join("")}`;
  els.labelFilter.value = selected;
}

/** Bind the queue filter controls and the debounced search box (once, at boot). */
export function bindQueueFilters() {
  if (!ctx?.els) return false;
  const { els, state, refreshAll } = ctx;
  els.refreshList.addEventListener("click", () => refreshAll());
  els.statusFilter.addEventListener("change", () => refreshAll());
  els.labelFilter.addEventListener("change", () => refreshAll());
  els.priorityFilter.addEventListener("change", () => refreshAll());
  els.ownershipFilter.addEventListener("change", () => refreshAll());
  if (els.channelFilter) els.channelFilter.addEventListener("change", () => refreshAll());
  if (els.sortFilter) els.sortFilter.addEventListener("change", () => refreshAll());
  els.focusWaiting.addEventListener("click", () => {
    els.ownershipFilter.value = els.ownershipFilter.value === "needs_response" ? "" : "needs_response";
    refreshAll();
  });
  els.searchInput.addEventListener("input", () => {
    window.clearTimeout(searchTimer);
    const delay = state.lowPerf ? 450 : 260;
    searchTimer = window.setTimeout(() => refreshAll({ silent: true, refreshDetail: false }), delay);
  });
  return true;
}

export default { renderLabelFilter, bindQueueFilters };
