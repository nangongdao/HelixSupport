/**
 * Helix Support — composer island bridge (D3 + tools slice)
 *
 * Routes the React composer island's interactions back to the legacy
 * composer lifecycle (drafts, operator send, copilot tools, canned macros,
 * pending attachments) and publishes helix-composer-state snapshots with
 * the tool data the island renders (canned responses, pending attachments,
 * canOperate). Kept in its own module so composer.js stays under the
 * 400-line gate.
 */

import {
  applyCopilotTone,
  clearDraft,
  fetchCopilotSuggestions,
  hideMacroSuggest,
  recordMacroUse,
  renderMacroSuggest,
  saveDraft,
  sendOperatorMessage,
} from "./composer.js?v=1.4.0";
import {
  removePendingAttachment,
  uploadPendingAttachment,
} from "./attachment.js?v=1.4.0";

let ctx = null;

/** Inject the legacy app.js singletons (state/els/canOperate). */
export function configure(deps) {
  ctx = deps;
}

/** Publish the current composer state for the React island mirror. The
 * tools (copilot bar, canned chips, pending attachments) are gated on
 * human + canOperate exactly like their legacy counterparts. */
export function publishComposerState() {
  if (typeof window === "undefined" || !window.__HELIX_ISLAND_MODE__) return;
  const detail = ctx.state?.detail?.conversation;
  const resolved = Boolean(detail && detail.status === "resolved");
  const human = Boolean(detail && ["waiting_human", "human_active"].includes(detail.status));
  const canOperate = Boolean(ctx.canOperate?.());
  const meta = window.HelixModules?.attachments?.attachmentMetaSnapshot?.() || {};
  const conversationId = ctx.state?.selectedId;
  const pending = conversationId
    ? (window.HelixModules?.attachments?.pendingIds?.(conversationId) || []).map((id) => ({
        id,
        filename: meta[id]?.filename || id,
      }))
    : [];
  window.dispatchEvent(
    new CustomEvent("helix-composer-state", {
      detail: {
        resolved,
        human,
        customerBusy: ctx.els?.customerForm?.dataset?.busy === "true",
        operatorBusy: ctx.els?.operatorForm?.dataset?.busy === "true",
        operatorDraft: ctx.els?.operatorInput?.value || "",
        canOperate,
        cannedResponses: Array.isArray(ctx.state?.cannedResponses) ? ctx.state.cannedResponses : [],
        pendingAttachments: pending,
      },
    }),
  );
}

/**
 * Bind the island bridge (exactly once, at app.js load). The React composer
 * island renders mirrored forms + tool surfaces in the desktop shell (legacy
 * forms yielded + hidden); route its interactions back to the legacy
 * send/draft/copilot/attachment lifecycle.
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
  // Copilot tools: the island passes its own textarea content where the
  // legacy flow would have read #operatorInput.
  window.addEventListener("helix-composer-copilot-suggest", (event) => {
    const { draft } = event.detail || {};
    void fetchCopilotSuggestions({ draft });
  });
  window.addEventListener("helix-composer-copilot-tone", (event) => {
    const { tone, text } = event.detail || {};
    if (tone) void applyCopilotTone(tone, { text });
  });
  // Macro usage tracking for canned chips / macro suggestions applied
  // island-side (the insertion itself is island-local).
  window.addEventListener("helix-composer-macro-use", (event) => {
    const { macroId } = event.detail || {};
    if (macroId) void recordMacroUse(macroId);
  });
  // Pending attachments: the island's file input hands the raw File across.
  window.addEventListener("helix-composer-attachment-upload", (event) => {
    const { file } = event.detail || {};
    if (file) void uploadPendingAttachment(file);
  });
  window.addEventListener("helix-composer-attachment-remove", (event) => {
    const { id } = event.detail || {};
    if (id) removePendingAttachment(ctx.state.selectedId, id);
  });
  // Browser dual-track: the legacy #operatorInput typing listener is the
  // exact counterpart of the helix-composer-typing bridge above (draft
  // autosave debounce + trailing-/ macro suggest), so both live here.
  ctx.els.operatorInput?.addEventListener("input", () => {
    const conversationId = ctx.state.selectedId;
    if (conversationId) {
      window.clearTimeout(ctx.state.draftTimer);
      ctx.state.draftTimer = window.setTimeout(() => {
        saveDraft(conversationId, ctx.els.operatorInput.value);
      }, 300);
    }
    const match = ctx.els.operatorInput.value.match(/(^|\s)\/([^\s]*)$/);
    if (match) renderMacroSuggest(match[2] || "");
    else hideMacroSuggest();
  });
  ctx.els.operatorInput?.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      ctx.els.operatorForm.requestSubmit();
      return;
    }
    if (event.key === "Escape" && ctx.state.macroOpen) {
      event.preventDefault();
      hideMacroSuggest();
    }
  });
  window.addEventListener("helix-composer-sync", () => publishComposerState());
  window.addEventListener("helix-queue-select", () => publishComposerState());
  return true;
}

export default { configure, publishComposerState, bindIslandBridge };
