/**
 * Helix Support — global keyboard shortcuts (app.js <500 campaign slice 24).
 *
 * §17.3 single-key shortcuts for the operator console: "/" focuses search,
 * "c" opens the new-conversation dialog, "r" refreshes, "i" toggles the
 * inspector, "l" toggles low-perf, and j/k walk the queue. Extracted from
 * app.js; state/els, refreshAll and selectConversation arrive through
 * configure.
 *
 * Typing is never hijacked: the handler bails out on form controls and on any
 * ctrl/meta/alt combination, which is what keeps Ctrl+K (palette) and the
 * browser's own shortcuts intact.
 */

let ctx = null;

/** Inject the legacy app.js singletons (state/els/refreshAll/selectConversation). */
export function configure(deps) {
  ctx = deps;
}

/** Bind the document-level shortcut handler (once, at boot). */
export function bindShortcuts() {
  if (!ctx?.els) return false;
  const { els, state, refreshAll, selectConversation } = ctx;
  document.addEventListener("keydown", (event) => {
    const target = event.target;
    const editing = target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement;
    if (editing || event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.key === "/") {
      event.preventDefault();
      els.searchInput.focus();
    } else if (event.key === "c") {
      event.preventDefault();
      els.newConversation.click();
    } else if (event.key === "r") {
      event.preventDefault();
      refreshAll();
    } else if (event.key === "i") {
      event.preventDefault();
      els.inspectorToggle?.click();
    } else if (event.key === "l") {
      event.preventDefault();
      els.lowPerfToggle?.click();
    } else if (["j", "k"].includes(event.key) && state.conversations.length) {
      event.preventDefault();
      const index = Math.max(0, state.conversations.findIndex((item) => item.id === state.selectedId));
      const next = Math.max(0, Math.min(state.conversations.length - 1, index + (event.key === "j" ? 1 : -1)));
      selectConversation(state.conversations[next].id);
    }
  });
  return true;
}

export default { bindShortcuts };
