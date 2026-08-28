/**
 * Helix Support — composer island bridge (D3)
 *
 * Routes the React composer island's interactions back to the legacy
 * composer lifecycle (drafts, operator send). Kept in its own module so
 * composer.js stays under the 400-line gate.
 */

import { sendOperatorMessage, saveDraft, clearDraft } from "./composer.js?v=1.4.0";

let ctx = null;

/** Inject the legacy app.js singletons (state/els/api/helpers). */
export function configure(deps) {
  ctx = deps;
}

/** Publish the current composer state for the React island mirror. */
export function publishComposerState() {
  if (typeof window === "undefined" || !window.__HELIX_ISLAND_MODE__) return;
  const detail = ctx.state?.detail?.conversation;
  const resolved = Boolean(detail && detail.status === "resolved");
  const human = Boolean(detail && ["waiting_human", "human_active"].includes(detail.status));
  window.dispatchEvent(
    new CustomEvent("helix-composer-state", {
      detail: {
        resolved,
        human,
        customerBusy: ctx.els?.customerForm?.dataset?.busy === "true",
        operatorBusy: ctx.els?.operatorForm?.dataset?.busy === "true",
        operatorDraft: ctx.els?.operatorInput?.value || "",
      },
    }),
  );
}

/**
 * Bind the island bridge (exactly once, at app.js load). The React composer
 * island renders mirrored forms in the desktop shell (legacy forms yielded +
 * hidden); route its interactions back to the legacy send/draft lifecycle.
 */
export function bindIslandBridge() {
  if (!ctx?.els) return false;
  window.addEventListener("helix-composer-submit", (event) => {
    const { kind, content } = event.detail || {};
    if (!content || !kind) return;
    if (kind === "operator") void sendOperatorMessage(content);
    // customer sends stay in app.js (same-named listener registered there).
  });
  window.addEventListener("helix-composer-typing", (event) => {
    const { kind, content } = event.detail || {};
    if (kind !== "operator" || typeof content !== "string") return;
    const conversationId = ctx.state.selectedId;
    if (!conversationId) return;
    ctx.els.operatorInput.value = content;
    window.clearTimeout(ctx.state.draftTimer);
    if (content.trim()) {
      ctx.state.draftTimer = window.setTimeout(() => saveDraft(conversationId, content), 300);
    } else {
      clearDraft(conversationId);
    }
  });
  window.addEventListener("helix-composer-sync", () => publishComposerState());
  window.addEventListener("helix-queue-select", () => publishComposerState());
  return true;
}

export default { configure, publishComposerState, bindIslandBridge };