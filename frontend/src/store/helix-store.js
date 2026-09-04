/**
 * Helix Support — Zustand store (D2)
 *
 * A module-level singleton that mirrors the legacy state.js shape so the
 * dual-track period (legacy app.js + React islands) can read/write the
 * same store without a bridge. The legacy code keeps its own `state`
 * object; this store is the React-facing read model.
 *
 * See DESKTOP_TAURI_PLAN.md §3.3.
 */

import { create } from "zustand";

/**
 * @typedef {Object} HelixState
 * @property {Array<object>} conversations - queue list
 * @property {string|null} activeId - selected conversation id
 * @property {string} queueSignature - stable signature for skip-re-render
 * @property {"workspace"|"quality"|"knowledge"|"admin"|"settings"} view
 * @property {"dark"|"light"} theme
 * @property {boolean} lowPerf - low-performance mode flag
 * @property {boolean} inspectorOpen
 * @property {string|null} error - last error message
 */

export const useStore = create((set, get) => ({
  conversations: [],
  activeId: null,
  queueSignature: "",
  view: "workspace",
  theme: "dark",
  lowPerf: false,
  inspectorOpen: true,
  error: null,

  setConversations: (conversations) =>
    set({
      conversations,
      queueSignature: computeSignature(conversations),
    }),

  setActiveId: (activeId) => set({ activeId }),
  setView: (view) => set({ view }),
  setTheme: (theme) => set({ theme }),
  setLowPerf: (lowPerf) => set({ lowPerf }),
  toggleInspector: () => set((s) => ({ inspectorOpen: !s.inspectorOpen })),
  setError: (error) => set({ error }),
  clearError: () => set({ error: null }),
}));

/**
 * Compute a stable signature of a conversation list so the UI can skip
 * re-rendering when nothing changed. Mirrors state.js queueSignature.
 * @param {Array<object>} conversations
 * @returns {string}
 */
function computeSignature(conversations) {
  if (!Array.isArray(conversations)) return "";
  return conversations
    .map((c) => `${c.id}:${c.status}:${c.updated_at}:${c.version ?? 0}`)
    .join("|");
}

export default useStore;
