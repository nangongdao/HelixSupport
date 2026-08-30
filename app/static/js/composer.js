/**
 * Helix Support — composer surface (ROADMAP §41.6 / ARC-001): drafts, claim
 * renewal, canned macros and the AI copilot bar. app.js keeps thin
 * delegating wrappers and calls configure() once with its singletons.
 */

import { clearPendingAttachments, pendingIds } from "./attachment.js?v=1.4.0";
import {
  clearDraft,
  loadDraft,
  saveDraft,
} from "./drafts.js?v=1.4.0";

let ctx = null;

/** Inject the legacy app.js singletons (state/els/api/helpers). */
export function configure(deps) {
  ctx = deps;
}

// Draft persistence lives in js/drafts.js (re-exported for the namespace).
export { clearDraft, draftKey, draftTtlMs, draftsEnabled, loadDraft, pruneExpiredDrafts, saveDraft } from "./drafts.js?v=1.4.0";

/** Publish a copilot section {status?, suggestions?, knowledge?, rewritten?}
 * to the composer island (no-op outside island mode). */
function publishCopilot(detail) {
  if (typeof window === "undefined" || !window.__HELIX_ISLAND_MODE__) return;
  window.dispatchEvent(new CustomEvent(COMPOSER_COPILOT_EVENT, { detail }));
}

/** Event the composer island listens on for copilot tool state. */
export const COMPOSER_COPILOT_EVENT = "helix-composer-copilot";

export function scheduleClaimRenewal(detail) {
  if (ctx.state.claimRenewTimer) {
    window.clearTimeout(ctx.state.claimRenewTimer);
    ctx.state.claimRenewTimer = null;
  }
  const conversation = detail?.conversation;
  if (!conversation?.claim_active || conversation.claimed_by !== ctx.state.me?.actor_id) return;
  if (!conversation.claim_expires_at) return;
  const expires = new Date(conversation.claim_expires_at).getTime();
  const delay = Math.max(15000, expires - Date.now() - 120000);
  ctx.state.claimRenewTimer = window.setTimeout(async () => {
    if (ctx.state.selectedId !== conversation.id) return;
    try {
      await ctx.api(`/api/conversations/${encodeURIComponent(conversation.id)}/claim`, {
        method: "POST",
      });
      if (ctx.state.selectedId === conversation.id) await ctx.loadDetail(conversation.id);
    } catch {
      // best-effort claim renewal
    }
  }, delay);
}

export function hideMacroSuggest() {
  ctx.state.macroOpen = false;
  if (ctx.els.macroSuggest) {
    ctx.els.macroSuggest.hidden = true;
    ctx.els.macroSuggest.innerHTML = "";
  }
}

export function renderMacroSuggest(query) {
  if (!ctx.els.macroSuggest || !ctx.canOperate()) {
    hideMacroSuggest();
    return;
  }
  const needle = (query || "").toLowerCase();
  const matches = ctx.state.cannedResponses
    .filter((item) => {
      const shortcut = (item.shortcut || "").toLowerCase();
      const title = (item.title || "").toLowerCase();
      return !needle || shortcut.includes(needle) || title.includes(needle);
    })
    .slice(0, 6);
  if (!matches.length) {
    hideMacroSuggest();
    return;
  }
  ctx.state.macroOpen = true;
  ctx.els.macroSuggest.hidden = false;
  ctx.els.macroSuggest.innerHTML = matches
    .map(
      (item) =>
        `<button class="macro-option" type="button" role="option" data-macro-id="${ctx.escapeHtml(item.id)}"><strong>${ctx.escapeHtml(item.title)}</strong><span>/${ctx.escapeHtml(item.shortcut || "—")}</span></button>`,
    )
    .join("");
}

export function applyMacroFromSuggest(responseId) {
  const macro = ctx.state.cannedResponses.find((item) => item.id === responseId);
  if (!macro) return;
  const value = ctx.els.operatorInput.value;
  const match = value.match(/(^|\s)\/([^\s]*)$/);
  if (match) {
    const start = value.slice(0, value.length - match[0].length + (match[1] ? match[1].length : 0));
    ctx.els.operatorInput.value = `${start}${macro.body}`;
  } else {
    ctx.els.operatorInput.value = macro.body;
  }
  hideMacroSuggest();
  saveDraft(ctx.state.selectedId, ctx.els.operatorInput.value);
  ctx.els.operatorInput.focus();
  void ctx.api(`/api/canned-responses/${encodeURIComponent(responseId)}/use`, { method: "POST" }).catch(
    () => {},
  );
}

/** Paint the canned chips (browser); island mode republishes state. */
export function renderCannedResponses() {
  if (typeof window !== "undefined" && window.__HELIX_ISLAND_MODE__) {
    window.HelixModules?.composerIslandBridge?.publishComposerState?.();
    return;
  }
  if (!ctx.els.cannedList) return;
  if (!ctx.state.cannedResponses.length) {
    ctx.els.cannedList.innerHTML = '<span class="canned-empty">暂无快捷回复</span>';
    return;
  }
  ctx.els.cannedList.innerHTML = ctx.state.cannedResponses
    .slice(0, 8)
    .map(
      (item) =>
        `<button class="canned-chip" type="button" data-macro-id="${ctx.escapeHtml(item.id)}" title="${ctx.escapeHtml(item.body)}">${ctx.escapeHtml(item.title)}${item.shortcut ? ` /${ctx.escapeHtml(item.shortcut)}` : ""}</button>`,
    )
    .join("");
}

export async function loadCannedResponses({ force = false } = {}) {
  if (!ctx.canOperate()) {
    ctx.state.cannedResponses = [];
    publishState();
    return;
  }
  if (!force && ctx.state.cannedLoadedAt && Date.now() - ctx.state.cannedLoadedAt < 120000) return;
  try {
    ctx.state.cannedResponses = await ctx.api("/api/canned-responses");
    ctx.state.cannedLoadedAt = Date.now();
  } catch {
    ctx.state.cannedResponses = [];
  }
  publishState();
}

/** Usage tracking for island-applied macros (best-effort, like legacy). */
export async function recordMacroUse(responseId) {
  try {
    await ctx.api(`/api/canned-responses/${encodeURIComponent(responseId)}/use`, { method: "POST" });
  } catch {
    // usage tracking is best-effort
  }
}

let copilotSuggestionTexts = [];

export function suggestionAt(index) {
  return copilotSuggestionTexts[Number(index)];
}

export function setCopilotStatus(text, autoHide = true) {
  if (!ctx.els.copilotStatus) return;
  ctx.els.copilotStatus.textContent = text;
  ctx.els.copilotStatus.hidden = !text;
  if (autoHide && text) {
    window.setTimeout(() => {
      if (ctx.els.copilotStatus) ctx.els.copilotStatus.hidden = true;
    }, 2500);
  }
}

export async function fetchCopilotSuggestions({ draft } = {}) {
  const conversationId = ctx.state.selectedId;
  if (!conversationId || !ctx.canOperate()) return;
  setCopilotStatus("生成中…", false);
  publishCopilot({ status: "生成中…" });
  try {
    const payload = await ctx.api("/api/copilot/suggest", {
      method: "POST",
      body: JSON.stringify({
        conversation_id: conversationId,
        // Island mode passes the island textarea content; legacy reads its own input.
        draft: draft !== undefined ? draft : ctx.els.operatorInput?.value || null,
      }),
    });
    const items = payload.suggestions || [];
    const mapped = items.map((item) => ({ content: item.content, source: item.source }));
    copilotSuggestionTexts = mapped.map((item) => item.content);
    if (typeof window !== "undefined" && window.__HELIX_ISLAND_MODE__) {
      publishCopilot({ suggestions: mapped, status: mapped.length ? "" : "暂无建议" });
      return;
    }
    if (!ctx.els.copilotSuggestions) return;
    if (!items.length) {
      ctx.els.copilotSuggestions.hidden = true;
      ctx.els.copilotSuggestions.innerHTML = "";
      setCopilotStatus("暂无建议");
      return;
    }
    ctx.els.copilotSuggestions.innerHTML = items
      .map((item, index) => {
        const badge = item.source === "model" ? "AI" : "模板";
        return `<button type="button" class="copilot-suggestion" data-index="${index}" title="${ctx.escapeHtml(item.content)}">`
          + `<span class="copilot-suggestion-badge">${badge}</span>`
          + `<span class="copilot-suggestion-text">${ctx.escapeHtml(item.content)}</span></button>`;
      })
      .join("");
    ctx.els.copilotSuggestions.hidden = false;
    setCopilotStatus("");
  } catch {
    setCopilotStatus("建议生成失败");
    publishCopilot({ status: "建议生成失败" });
  }
}

export async function loadCopilotKnowledge() {
  const conversationId = ctx.state.selectedId;
  if (!conversationId || !ctx.canOperate()) return;
  if (ctx.state.lastCopilotConv === conversationId) return;
  ctx.state.lastCopilotConv = conversationId;
  try {
    const payload = await ctx.api("/api/copilot/knowledge", {
      method: "POST",
      body: JSON.stringify({ conversation_id: conversationId }),
    });
    const articles = (payload.articles || []).map((article) => ({
      title: article.title,
      category: article.category || "",
    }));
    if (typeof window !== "undefined" && window.__HELIX_ISLAND_MODE__) {
      publishCopilot({ knowledge: articles });
      return;
    }
    if (!ctx.els.copilotKnowledge) return;
    ctx.els.copilotKnowledge.hidden = !articles.length;
    ctx.els.copilotKnowledge.innerHTML = articles
      .map((article) => `<button type="button" class="copilot-kb-item" data-title="${ctx.escapeHtml(article.title)}">`
        + `<span class="copilot-kb-title">${ctx.escapeHtml(article.title)}</span>`
        + `<span class="copilot-kb-cat">${ctx.escapeHtml(article.category)}</span></button>`)
      .join("");
  } catch {
    if (typeof window !== "undefined" && window.__HELIX_ISLAND_MODE__) {
      publishCopilot({ knowledge: [] });
      return;
    }
    if (ctx.els.copilotKnowledge) ctx.els.copilotKnowledge.hidden = true;
  }
}

export async function applyCopilotTone(tone, { text } = {}) {
  const value = text !== undefined ? text : ctx.els.operatorInput?.value || "";
  if (!value.trim()) {
    setCopilotStatus("先输入草稿再改写");
    publishCopilot({ status: "先输入草稿再改写" });
    return;
  }
  setCopilotStatus("改写中…", false);
  publishCopilot({ status: "改写中…" });
  try {
    const payload = await ctx.api("/api/copilot/rewrite", {
      method: "POST",
      body: JSON.stringify({ text: value, tone }),
    });
    const statusText = payload.source === "model" ? "已改写" : "原样保留（模型不可用）";
    if (typeof window !== "undefined" && window.__HELIX_ISLAND_MODE__) {
      publishCopilot({ status: statusText, rewritten: payload.rewritten });
      return;
    }
    ctx.els.operatorInput.value = payload.rewritten;
    setCopilotStatus(statusText);
  } catch {
    setCopilotStatus("改写失败");
    publishCopilot({ status: "改写失败" });
  }
}

export function resetCopilot() {
  ctx.state.lastCopilotConv = null;
  copilotSuggestionTexts = [];
  if (ctx.els.copilotSuggestions) {
    ctx.els.copilotSuggestions.hidden = true;
    ctx.els.copilotSuggestions.innerHTML = "";
  }
  if (ctx.els.copilotKnowledge) {
    ctx.els.copilotKnowledge.hidden = true;
    ctx.els.copilotKnowledge.innerHTML = "";
  }
  if (ctx.els.copilotStatus) ctx.els.copilotStatus.hidden = true;
}

export function renderCopilot(detail) {
  const conversation = detail.conversation;
  const human = ["waiting_human", "human_active"].includes(conversation.status);
  const visible = human && ctx.canOperate();
  // Island mode: the composer island derives the copilot/canned/attachment
  // tool visibility from the composer state snapshot; the knowledge load and
  // reset lifecycle stay here.
  if (typeof window !== "undefined" && window.__HELIX_ISLAND_MODE__) {
    if (!visible) {
      resetCopilot();
      publishCopilot({ suggestions: [], knowledge: [], status: "" });
    } else if (ctx.state.lastCopilotConv !== conversation.id) {
      void loadCopilotKnowledge();
    }
    publishState();
    return;
  }
  if (!ctx.els.copilotBar) return;
  ctx.els.copilotBar.hidden = !visible;
  if (ctx.els.copilotBar.hidden) {
    resetCopilot();
    return;
  }
  if (ctx.state.lastCopilotConv !== conversation.id) void loadCopilotKnowledge();
}

/** Republish the composer state snapshot to the island (no-op outside
 * island mode; the bridge module owns the payload). */
function publishState() {
  if (typeof window === "undefined" || !window.__HELIX_ISLAND_MODE__) return;
  window.HelixModules?.composerIslandBridge?.publishComposerState?.();
}

/** Send an operator message (shared by the legacy form and the React island
 *  bridge). Reads nothing from the DOM — the caller passes the content. */
export async function sendOperatorMessage(content) {
  const conversationId = ctx.state.selectedId;
  if (!conversationId) return;
  const text = String(content || "").trim();
  if (!text) return;
  hideMacroSuggest();
  ctx.setFormBusy(ctx.els.operatorForm, true);
  try {
    const body = { content: text };
    // Backlog (语音/富媒体消息): include this conversation's pending uploads.
    const pendingIds_ = pendingIds(conversationId);
    if (pendingIds_.length) body.attachment_ids = [...pendingIds_];
    await ctx.api(`/api/conversations/${encodeURIComponent(conversationId)}/operator-messages`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    clearPendingAttachments(conversationId);
    ctx.els.operatorInput.value = "";
    clearDraft(conversationId);
    await ctx.loadDetail(conversationId);
    void ctx.refreshAll({ silent: true, refreshDetail: false });
  } catch (error) {
    ctx.showToast(error.message, true);
  } finally {
    ctx.setFormBusy(ctx.els.operatorForm, false);
  }
}

/**
 * Bind the composer DOM (exactly once, at app.js load): operator message
 * submit, copilot suggestion chips, tone rewrite and knowledge picks.
 */
export function bindComposer() {
  if (!ctx?.els) return false;
  if (ctx.els.operatorForm) {
    ctx.els.operatorForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!ctx.state.selectedId) return;
      const content = ctx.els.operatorInput.value.trim();
      if (!content) return;
      await sendOperatorMessage(content);
    });
  }
  if (ctx.els.copilotSuggestBtn) {
    ctx.els.copilotSuggestBtn.addEventListener("click", () => void fetchCopilotSuggestions());
  }
  if (ctx.els.copilotTone) {
    ctx.els.copilotTone.addEventListener("change", () => {
      const tone = ctx.els.copilotTone.value;
      ctx.els.copilotTone.value = "";
      if (tone) void applyCopilotTone(tone);
    });
  }
  if (ctx.els.copilotSuggestions) {
    ctx.els.copilotSuggestions.addEventListener("click", (event) => {
      const button = event.target.closest(".copilot-suggestion");
      if (!button) return;
      const content = suggestionAt(button.dataset.index);
      if (content) {
        ctx.els.operatorInput.value = content;
        ctx.els.operatorInput.focus();
      }
    });
  }
  if (ctx.els.copilotKnowledge) {
    ctx.els.copilotKnowledge.addEventListener("click", (event) => {
      const button = event.target.closest(".copilot-kb-item");
      if (!button) return;
      ctx.els.operatorInput.value = button.dataset.title || "";
      ctx.els.operatorInput.focus();
    });
  }
  return true;
}