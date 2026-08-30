/**
 * Helix Support — conversation write actions (app.js <500 campaign slice 18).
 *
 * The operator's write paths on a conversation: the simulated customer
 * message send (shared by the legacy form and the composer island's
 * helix-composer-submit bridge), the claim/release/assign/accept/resolve/
 * reopen lifecycle buttons (performConversationAction owns the resolve CSAT
 * banner), the manual language override, and the CSAT link copy.
 * Extracted from the legacy app.js; no wrappers remain — boot binds once.
 */

let ctx = null;

/** Mirror of the legacy app.js key format (ui- prefix + uuid/uptime). */
function newIdempotencyKey() {
  if (window.crypto?.randomUUID) return `ui-${window.crypto.randomUUID()}`;
  return `ui-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/** Inject the legacy app.js singletons (state/els/api/helpers). */
export function configure(deps) {
  ctx = deps;
}

export async function sendCustomerMessage(content) {
  if (!ctx.state.selectedId) return;
  const conversationId = ctx.state.selectedId;
  const text = String(content || "").trim();
  if (!text) return;
  ctx.setFormBusy(ctx.els.customerForm, true);
  try {
    const result = await ctx.api(
      `/api/conversations/${encodeURIComponent(conversationId)}/messages`,
      {
        method: "POST",
        headers: { "Idempotency-Key": newIdempotencyKey() },
        body: JSON.stringify({ content: text }),
      },
    );
    ctx.els.customerInput.value = "";
    if (!result.assistant_message) ctx.showToast("客户消息已进入人工队列");
    await ctx.loadDetail(conversationId);
    void ctx.refreshAll({ silent: true, refreshDetail: false });
  } catch (error) {
    ctx.showToast(error.message, true);
  } finally {
    ctx.setFormBusy(ctx.els.customerForm, false);
  }
}

export async function performConversationAction(action, successMessage) {
  if (!ctx.state.selectedId) return;
  const conversationId = ctx.state.selectedId;
  try {
    const result = await ctx.api(`/api/conversations/${encodeURIComponent(conversationId)}/${action}`, {
      method: "POST",
    });
    if (action === "resolve") {
      const surveyUrl = result && result.survey_url;
      if (surveyUrl) {
        ctx.els.csatUrl.textContent = surveyUrl;
        ctx.els.csatBanner.hidden = false;
      } else {
        ctx.els.csatBanner.hidden = true;
      }
    }
    ctx.showToast(successMessage);
    await ctx.loadDetail(conversationId);
    await ctx.refreshAll({ refreshDetail: false });
  } catch (error) {
    ctx.showToast(error.message, true);
  }
}

/**
 * Bind the conversation action controls (exactly once, at boot). The
 * customer send also serves the composer island's submit bridge (the island
 * keeps the operator/note paths in their own domains).
 */
export function bindConversationActions() {
  if (!ctx?.els) return false;
  ctx.els.customerForm.addEventListener("submit", (event) => {
    event.preventDefault();
    void sendCustomerMessage(ctx.els.customerInput.value);
  });
  window.addEventListener("helix-composer-submit", (event) => {
    const { kind, content } = event.detail || {};
    if (!content || !kind) return;
    if (kind === "customer") void sendCustomerMessage(content);
  });
  ctx.els.claimBtn.addEventListener("click", () => performConversationAction("claim", "会话已认领"));
  if (ctx.els.conversationLanguageSelect) {
    // Backlog (多语言客服): PATCH the manual override; the select rolls back on
    // failure and a background refresh re-syncs the whole view.
    ctx.els.conversationLanguageSelect.addEventListener("change", async () => {
      if (!ctx.state.selectedId) return;
      const language = ctx.els.conversationLanguageSelect.value || null;
      try {
        await ctx.api(`/api/conversations/${encodeURIComponent(ctx.state.selectedId)}/language`, {
          method: "PATCH",
          body: JSON.stringify({ language }),
        });
        if (ctx.state.detail) ctx.state.detail.conversation.language = language;
        ctx.renderSubtitle(ctx.state.detail.conversation);
        ctx.renderLanguagePicker(ctx.state.detail.conversation);
        ctx.showToast(
          language
            ? `会话语言已设为 ${ctx.languageNames[language] || language}`
            : "会话语言已恢复自动检测",
        );
        ctx.scheduleIdle(() => ctx.refreshAll());
      } catch (error) {
        if (ctx.state.detail) ctx.renderLanguagePicker(ctx.state.detail.conversation);
        ctx.showToast(error.message, true);
      }
    });
  }
  ctx.els.releaseBtn.addEventListener("click", () => performConversationAction("release", "认领已释放"));
  if (ctx.els.assignBtn) {
    ctx.els.assignBtn.addEventListener("click", async () => {
      if (!ctx.state.selectedId || !ctx.state.me?.actor_id) return;
      const conversationId = ctx.state.selectedId;
      try {
        await ctx.api(`/api/conversations/${encodeURIComponent(conversationId)}/assign`, {
          method: "POST",
          body: JSON.stringify({ assignee_id: ctx.state.me.actor_id }),
        });
        ctx.showToast("会话已转派给自己");
        await ctx.loadDetail(conversationId);
        await ctx.refreshAll({ refreshDetail: false });
      } catch (error) {
        ctx.showToast(error.message, true);
      }
    });
  }
  ctx.els.acceptBtn.addEventListener("click", () => performConversationAction("accept", "会话已接入"));
  ctx.els.resolveBtn.addEventListener("click", () => performConversationAction("resolve", "会话已解决"));
  ctx.els.csatCopyBtn.addEventListener("click", async () => {
    const url = ctx.els.csatUrl.textContent;
    if (!url) return;
    try {
      await navigator.clipboard.writeText(url);
      ctx.showToast("满意度链接已复制");
    } catch (error) {
      window.prompt("请手动复制满意度链接：", url);
    }
  });
  ctx.els.reopenBtn.addEventListener("click", () => performConversationAction("reopen", "会话已重开"));
  return true;
}

export default {
  sendCustomerMessage,
  performConversationAction,
  bindConversationActions,
};
