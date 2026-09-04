/**
 * Helix Support — saved queue views (app.js <500 campaign slice 21 + 23).
 *
 * Owns the whole saved-views domain: the filter snapshot, the legacy
 * <select> render, the list reload, applying a stored view back onto the
 * inputs, the CRUD lifecycle and the legacy bindings plus island bridges.
 * Extracted from app.js; state/els/api-level primitives and the
 * refreshAll/escapeHtml helpers arrive through configure.
 *
 * Dual-track: in a browser tab this module binds the legacy controls; in the
 * desktop shell the island owns them and drives helix-saved-views-* bridges.
 */

let ctx = null;

/** Inject the legacy app.js singletons (state/els/api/helpers). */
export function configure(deps) {
  ctx = deps;
}

/** Filter snapshot stored with a saved view. */
export function currentViewFilters() {
  const { els } = ctx;
  return {
    ...(els.searchInput.value.trim() ? { search: els.searchInput.value.trim() } : {}),
    ...(els.statusFilter.value ? { status: els.statusFilter.value } : {}),
    ...(els.labelFilter.value ? { label: els.labelFilter.value } : {}),
    ...(els.priorityFilter.value ? { priority: els.priorityFilter.value } : {}),
    ...(els.channelFilter?.value ? { channel: els.channelFilter.value } : {}),
    ...(els.sortFilter?.value && els.sortFilter.value !== "priority"
      ? { sort: els.sortFilter.value }
      : {}),
    ...(els.ownershipFilter.value ? { ownership: els.ownershipFilter.value } : {}),
  };
}

/** Redraw the <select>, keeping a still-valid selection. */
export function renderSavedViews() {
  const { els, state, escapeHtml } = ctx;
  const selected = els.savedViewSelect.value;
  els.savedViewSelect.innerHTML = `<option value="">保存的视图</option>${state.savedViews
    .map((view) => `<option value="${escapeHtml(view.id)}">${escapeHtml(view.name)}</option>`)
    .join("")}`;
  els.savedViewSelect.value = state.savedViews.some((view) => view.id === selected) ? selected : "";
  els.deleteView.disabled = !els.savedViewSelect.value;
}

export async function loadSavedViews() {
  // Island mode: the saved-views island fetches /api/saved-views itself and
  // the yielded legacy select stays empty; apply receives the view object
  // through the bridge, so state.savedViews is not needed.
  if (typeof window !== "undefined" && window.__HELIX_ISLAND_MODE__) return;
  try {
    ctx.state.savedViews = await ctx.api("/api/saved-views");
  } catch {
    ctx.state.savedViews = [];
  }
  renderSavedViews();
}

/** Push a stored view's filters back onto the inputs and reload the queue. */
export function applySavedView(view) {
  const { els } = ctx;
  const filters = view?.filters || {};
  els.searchInput.value = filters.search || "";
  els.statusFilter.value = filters.status || "";
  els.labelFilter.value = filters.label || "";
  els.priorityFilter.value = filters.priority || "";
  if (els.channelFilter) els.channelFilter.value = filters.channel || "";
  if (els.sortFilter) els.sortFilter.value = filters.sort || "priority";
  els.ownershipFilter.value = filters.ownership || "";
  els.focusWaiting.setAttribute("aria-pressed", String(filters.ownership === "needs_response"));
  ctx.refreshAll();
}

export async function saveSavedView(name) {
  try {
    const created = await ctx.api("/api/saved-views", {
      method: "POST",
      body: JSON.stringify({ name: name.trim(), filters: currentViewFilters() }),
    });
    await loadSavedViews();
    if (typeof window === "undefined" || !window.__HELIX_ISLAND_MODE__) {
      ctx.els.savedViewSelect.value = created.id;
      ctx.els.deleteView.disabled = false;
    }
    ctx.showToast("视图已保存");
    return created.id;
  } catch (error) {
    ctx.showToast(error.message, true);
    return null;
  }
}

export async function deleteSavedView(viewId) {
  if (!viewId) return false;
  try {
    await ctx.request(`/api/saved-views/${encodeURIComponent(viewId)}`, { method: "DELETE" });
    await loadSavedViews();
    ctx.showToast("视图已删除");
    return true;
  } catch (error) {
    ctx.showToast(error.message, true);
    return false;
  }
}

/** Bind the legacy saved-views controls and the island bridges (exactly
 * once, at boot). */
export function bindSavedViews() {
  if (!ctx?.els) return false;
  ctx.els.savedViewSelect.addEventListener("change", () => {
    const view = ctx.state.savedViews.find((item) => item.id === ctx.els.savedViewSelect.value);
    ctx.els.deleteView.disabled = !view;
    if (view) applySavedView(view);
  });
  ctx.els.saveView.addEventListener("click", async () => {
    const name = window.prompt("保存视图名称");
    if (!name?.trim()) return;
    await saveSavedView(name);
  });
  ctx.els.deleteView.addEventListener("click", async () => {
    await deleteSavedView(ctx.els.savedViewSelect.value);
  });
  // D3 bridge (saved views island): the island owns the select/save/delete
  // controls; apply receives the view object (the island holds the data),
  // save reads currentViewFilters() here where the filter inputs live, and
  // every mutation reports back so the island refetches and reselects.
  window.addEventListener("helix-saved-views-apply", (event) => {
    const { view } = event.detail || {};
    if (view) applySavedView(view);
  });
  window.addEventListener("helix-saved-views-save", async (event) => {
    const { name } = event.detail || {};
    if (!name) return;
    const createdId = await saveSavedView(name);
    window.dispatchEvent(new CustomEvent("helix-saved-views-changed", {
      detail: { ok: createdId != null, id: createdId },
    }));
  });
  window.addEventListener("helix-saved-views-delete", async (event) => {
    const { id } = event.detail || {};
    if (!id) return;
    const ok = await deleteSavedView(id);
    window.dispatchEvent(new CustomEvent("helix-saved-views-changed", { detail: { ok } }));
  });
  return true;
}

export default { currentViewFilters, renderSavedViews, loadSavedViews, applySavedView, saveSavedView, deleteSavedView, bindSavedViews };
