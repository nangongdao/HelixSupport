/**
 * Helix Support — saved views island (D3 long tail).
 *
 * Owns the workspace saved-views controls in the desktop shell: the view
 * select, the save button and the delete button. Legacy app.js keeps the
 * data lifecycle — apply writes the legacy filter inputs and refreshes,
 * save reads currentViewFilters() (the legacy inputs are the source of
 * truth), delete removes by id — via the bridges:
 *   helix-saved-views-apply   {view} → legacy applySavedView(view)
 *   helix-saved-views-save    {name} → legacy POST + toast
 *   helix-saved-views-delete  {id}   → legacy DELETE + toast
 *   helix-saved-views-changed {ok, id?} ← legacy reports save/delete done
 * The island fetches /api/saved-views itself (legacy loadSavedViews skips
 * in island mode) and refetches on the changed event.
 *
 * The prompt UX and every class/id contract mirror legacy; element ids get
 * a React suffix because the yielded legacy tree keeps the originals.
 *
 * See DESKTOP_TAURI_PLAN.md §D3 (app.js long tail).
 */

import React, { useCallback, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  QueryClient,
  QueryClientProvider,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

export const SAVED_VIEW_EVENTS = Object.freeze({
  APPLY: "helix-saved-views-apply",
  SAVE: "helix-saved-views-save",
  DELETE: "helix-saved-views-delete",
  CHANGED: "helix-saved-views-changed",
});

/** React-suffixed ids: the yielded legacy controls keep the originals. */
export const SAVED_VIEW_IDS = Object.freeze({
  select: "savedViewSelectReact",
  save: "saveViewReact",
  remove: "deleteViewReact",
});

export const SAVED_VIEW_PLACEHOLDER = "保存的视图";

async function fetchSavedViews() {
  const tenant = document.documentElement.dataset.tenantId || "demo";
  const res = await fetch("/api/saved-views", { headers: { "X-Tenant-Id": tenant } });
  if (!res.ok) throw new Error(`saved views API ${res.status}`);
  return res.json();
}

export function SavedViewsIsland() {
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState("");
  const query = useQuery({
    queryKey: ["saved-views"],
    queryFn: fetchSavedViews,
    // Legacy loads once at boot and after writes; the island refetches on
    // the changed event instead of any staleness window.
    staleTime: Infinity,
  });
  const views = Array.isArray(query.data) ? query.data : [];

  const onChanged = useCallback((event) => {
    const { ok, id } = event.detail || {};
    if (ok === false) return;
    // Save carries the created id to reselect; delete carries none and the
    // selection must drop (the view no longer exists).
    setSelectedId(id || "");
    void queryClient.invalidateQueries({ queryKey: ["saved-views"] });
  }, [queryClient]);

  useEffect(() => {
    window.addEventListener(SAVED_VIEW_EVENTS.CHANGED, onChanged);
    return () => window.removeEventListener(SAVED_VIEW_EVENTS.CHANGED, onChanged);
  }, [onChanged]);

  const apply = (event) => {
    const viewId = event.target.value;
    setSelectedId(viewId);
    const view = views.find((item) => item.id === viewId);
    if (view) {
      window.dispatchEvent(new CustomEvent(SAVED_VIEW_EVENTS.APPLY, { detail: { view } }));
    }
  };

  const save = () => {
    const name = window.prompt("保存视图名称");
    if (!name?.trim()) return;
    window.dispatchEvent(new CustomEvent(SAVED_VIEW_EVENTS.SAVE, { detail: { name: name.trim() } }));
  };

  const remove = () => {
    if (!selectedId) return;
    window.dispatchEvent(new CustomEvent(SAVED_VIEW_EVENTS.DELETE, { detail: { id: selectedId } }));
  };

  return (
    <>
      <label className="saved-view-field">
        <span className="sr-only">已保存视图</span>
        <select
          id={SAVED_VIEW_IDS.select}
          aria-label="已保存视图"
          value={selectedId}
          onChange={apply}
        >
          <option value="">{SAVED_VIEW_PLACEHOLDER}</option>
          {views.map((view) => (
            <option value={view.id} key={view.id}>{view.name}</option>
          ))}
        </select>
      </label>
      <button
        id={SAVED_VIEW_IDS.save}
        className="workspace-icon-button"
        type="button"
        title="保存当前视图"
        aria-label="保存当前视图"
        onClick={save}
      >
        <svg className="icon"><use href="/static/icons.svg?v=1.4.0#check" /></svg>
      </button>
      <button
        id={SAVED_VIEW_IDS.remove}
        className="workspace-icon-button"
        type="button"
        title="删除当前视图"
        aria-label="删除当前视图"
        disabled={!selectedId}
        onClick={remove}
      >
        <svg className="icon"><use href="/static/icons.svg?v=1.4.0#x" /></svg>
      </button>
    </>
  );
}

/**
 * Mount the saved views island into a host <div>. Called by the island
 * loader. The island uses useQueryClient, so the provider is required —
 * without it React throws "No QueryClient set" and the mount stays empty
 * (component tests wrap their own provider and cannot catch this; only the
 * desktop boot path exercises the bare mount).
 * @param {HTMLElement} element - mount point
 */
export function mount(element) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { refetchOnWindowFocus: false } },
  });
  const root = createRoot(element);
  root.render(
    <QueryClientProvider client={queryClient}>
      <SavedViewsIsland />
    </QueryClientProvider>,
  );
}

export default { mount, SavedViewsIsland };
