/**
 * Helix Support — composer surface (ROADMAP §41.6 / ARC-001).
 *
 * Local drafts, claim renewal, canned-response macros, and the AI copilot
 * bar. Extracted from the legacy app.js; app.js keeps thin delegating
 * wrappers with identical names/signatures, so the running UI behaviour is
 * unchanged. app.js calls configure() once at load time with its singletons
 * (state/els/api/…) because this module binds no DOM at import time.
 */

import { clearPendingAttachments, pendingIds } from "./attachment.js?v=1.3.7";

let ctx = null;

/** Inject the legacy app.js singletons (state/els/api/helpers). */
export function configure(deps) {
  ctx = deps;
}

const DRAFT_PREFIX = "helix-draft:";

export function draftKey(conversationId) {
  return `${DRAFT_PREFIX}${conversationId}`;
}

export function draftsEnabled() {
  return ctx.state.me?.local_drafts_enabled !== false;
}

export function draftTtlMs() {
  const minutes = Number(ctx.state.me?.local_draft_ttl_minutes) || 720;
  return minutes * 60000;
}

export function loadDraft(conversationId) {
  if (!conversationId || !draftsEnabled()) return "";
  try {
    const raw = window.localStorage.getItem(draftKey(conversationId));
    if (!raw) return "";
    const parsed = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null || typeof parsed.text !== "string") {
      window.localStorage.removeItem(draftKey(conversationId));
      return "";
    }
    if (Date.now() - Number(parsed.savedAt || 0) > draftTtlMs()) {
      window.localStorage.removeItem(draftKey(conversationId));
      return "";
    }
    return parsed.text;
  } catch {
    return "";
  }
}

export function saveDraft(conversationId, value) {
  if (!conversationId || !draftsEnabled()) return;
  try {
    if (!value.trim()) window.localStorage.removeItem(draftKey(conversationId));
    else
      window.localStorage.setItem(
        draftKey(conversationId),
        JSON.stringify({ text: value, savedAt: Date.now() }),
      );
  } catch {
    // localStorage may be unavailable
  }
}

export function clearDraft(conversationId) {
  if (!conversationId) return;
  try {
    window.localStorage.removeItem(draftKey(conversationId));
  } catch {
    // localStorage may be unavailable
  }
}

export function pruneExpiredDrafts() {
  try {
    const ttl = draftTtlMs();
    const stale = [];
    for (let i = 0; i < window.localStorage.length; i += 1) {
      const key = window.localStorage.key(i);
      if (!key || !key.startsWith(DRAFT_PREFIX)) continue;
      if (!draftsEnabled()) {
        stale.push(key);
        continue;
      }
      try {
        const parsed = JSON.parse(window.localStorage.getItem(key) || "");
        if (typeof parsed?.text !== "string" || Date.now() - Number(parsed.savedAt || 0) > ttl) {
          stale.push(key);
        }
      } catch {
        stale.push(key);
      }
    }
    for (const key of stale) window.localStorage.removeItem(key);
  } catch {
    // localStorage may be unavailable
  }
}

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

export async function loadCannedResponses({ force = false } = {}) {
  if (!ctx.canOperate()) {
    ctx.state.cannedResponses = [];
    return;
  }
  if (!force && ctx.state.cannedLoadedAt && Date.now() - ctx.state.cannedLoadedAt < 120000) return;
  try {
    ctx.state.cannedResponses = await ctx.api("/api/canned-responses");
    ctx.state.cannedLoadedAt = Date.now();
  } catch {
    ctx.state.cannedResponses = [];
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

export async function fetchCopilotSuggestions() {
  const conversationId = ctx.state.selectedId;
  if (!conversationId || !ctx.canOperate()) return;
  setCopilotStatus("生成中…", false);
  try {
    const payload = await ctx.api("/api/copilot/suggest", {
      method: "POST",
      body: JSON.stringify({
        conversation_id: conversationId,
        draft: ctx.els.operatorInput?.value || null,
      }),
    });
    const items = payload.suggestions || [];
    if (!ctx.els.copilotSuggestions) return;
    if (!items.length) {
      ctx.els.copilotSuggestions.hidden = true;
      ctx.els.copilotSuggestions.innerHTML = "";
      setCopilotStatus("暂无建议");
      return;
    }
    copilotSuggestionTexts = items.map((item) => item.content);
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
  }
}

export async function loadCopilotKnowledge() {
  const conversationId = ctx.state.selectedId;
  if (!conversationId || !ctx.canOperate()) return;
  if (ctx.state.lastCopilotConv === conversationId) return;
  ctx.state.lastCopilotConv = conversationId;
  if (!ctx.els.copilotKnowledge) return;
  try {
    const payload = await ctx.api("/api/copilot/knowledge", {
      method: "POST",
      body: JSON.stringify({ conversation_id: conversationId }),
    });
    const articles = payload.articles || [];
    ctx.els.copilotKnowledge.hidden = !articles.length;
    ctx.els.copilotKnowledge.innerHTML = articles
      .map((article) => `<button type="button" class="copilot-kb-item" data-title="${ctx.escapeHtml(article.title)}">`
        + `<span class="copilot-kb-title">${ctx.escapeHtml(article.title)}</span>`
        + `<span class="copilot-kb-cat">${ctx.escapeHtml(article.category || "")}</span></button>`)
      .join("");
  } catch {
    ctx.els.copilotKnowledge.hidden = true;
  }
}

export async function applyCopilotTone(tone) {
  const text = ctx.els.operatorInput?.value || "";
  if (!text.trim()) {
    setCopilotStatus("先输入草稿再改写");
    return;
  }
  setCopilotStatus("改写中…", false);
  try {
    const payload = await ctx.api("/api/copilot/rewrite", {
      method: "POST",
      body: JSON.stringify({ text, tone }),
    });
    ctx.els.operatorInput.value = payload.rewritten;
    setCopilotStatus(payload.source === "model" ? "已改写" : "原样保留（模型不可用）");
  } catch {
    setCopilotStatus("改写失败");
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
  if (!ctx.els.copilotBar) return;
  const conversation = detail.conversation;
  const human = ["waiting_human", "human_active"].includes(conversation.status);
  ctx.els.copilotBar.hidden = !human || !ctx.canOperate();
  if (ctx.els.copilotBar.hidden) {
    resetCopilot();
    return;
  }
  if (ctx.state.lastCopilotConv !== conversation.id) void loadCopilotKnowledge();
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
      const conversationId = ctx.state.selectedId;
      const content = ctx.els.operatorInput.value.trim();
      if (!content) return;
      hideMacroSuggest();
      ctx.setFormBusy(ctx.els.operatorForm, true);
      try {
        const body = { content };
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