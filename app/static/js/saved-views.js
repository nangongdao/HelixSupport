/**
 * Helix Support — saved queue views (app.js <500 campaign slice 21).
 *
 * The saved-views CRUD lifecycle: save (POST with the current filter
 * snapshot), delete (DELETE via the raw request path), and the legacy
 * select/save/delete bindings plus the saved-views island bridges. Extracted
 * from the legacy app.js; the filter-snapshot (currentViewFilters), apply
 * and list-reload stay in app.js where the filter inputs and queue render
 * live, and arrive through configure.
 *
 * Dual-track: in a plain browser tab this module binds the legacy controls;
 * in the desktop shell the saved-views island owns the select/save/delete
 * DOM and drives the same lifecycle through helix-saved-views-* bridges.
 */

let ctx = null;

/** Inject the legacy app.js singletons (state/els/api/helpers). */
export function configure(deps) {
  ctx = deps;
}

export async function saveSavedView(name) {
  try {
    const created = await ctx.api("/api/saved-views", {
      method: "POST",
      body: JSON.stringify({ name: name.trim(), filters: ctx.currentViewFilters() }),
    });
    await ctx.loadSavedViews();
    if (!window.__HELIX_ISLAND_MODE__) {
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
    await ctx.loadSavedViews();
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
    if (view) ctx.applySavedView(view);
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
    if (view) ctx.applySavedView(view);
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

export default { saveSavedView, deleteSavedView, bindSavedViews };
