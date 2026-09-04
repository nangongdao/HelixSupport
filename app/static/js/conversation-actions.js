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
  performConversationActions: performConversationAction,
  bindConversationActions,
  openNewConversationDialog,
  closeConversationDialog,
  createConversation,
  bindConversationDialog,
};

// ── New-conversation dialog lifecycle (app.js <500 slice 26) ──

export function openNewConversationDialog() {
  // Island mode: the conversation dialog island owns the <dialog>; hand the
  // open over and let the island dispatch helix-conversation-create.
  if (window.__HELIX_ISLAND_MODE__) {
    window.dispatchEvent(new CustomEvent("helix-conversation-new"));
    return;
  }
  ctx.els.newConversationForm.reset();
  ctx.els.newConversationDialog.showModal();
  window.setTimeout(() => ctx.els.newCustomerName.focus(), 0);
}

export function closeConversationDialog() {
  ctx.els.newConversationDialog.close();
}

export async function createConversation(payload) {
  ctx.setFormBusy(ctx.els.newConversationForm, true);
  try {
    const created = await ctx.api("/api/conversations", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    if (!window.__HELIX_ISLAND_MODE__) closeConversationDialog();
    ctx.state.selectedId = created.id;
    ctx.state.conversations = [
      created,
      ...ctx.state.conversations.filter((conversation) => conversation.id !== created.id),
    ];
    ctx.actions.renderQueue();
    await ctx.loadDetail(created.id);
    void ctx.refreshAll({ silent: true, refreshDetail: false });
    return true;
  } catch (error) {
    ctx.showToast(error.message, true);
    return false;
  } finally {
    ctx.setFormBusy(ctx.els.newConversationForm, false);
  }
}

export function bindConversationDialog() {
  if (!ctx?.els) return false;
  ctx.els.newConversation.addEventListener("click", openNewConversationDialog);
  ctx.els.closeDialog.addEventListener("click", closeConversationDialog);
  ctx.els.cancelDialog.addEventListener("click", closeConversationDialog);
  ctx.els.newConversationForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = {
      customer_name: ctx.els.newCustomerName.value.trim(),
      channel: ctx.els.newChannel.value,
    };
    const customerRef = ctx.els.newCustomerRef.value.trim();
    if (customerRef) payload.customer_ref = customerRef;
    if (!payload.customer_name) return;
    await createConversation(payload);
  });
  // D3 bridge (conversation dialog island): the island reports the form
  // outcome via helix-conversation-created so it can close on success.
  window.addEventListener("helix-conversation-create", async (event) => {
    const { payload } = event.detail || {};
    if (!payload) return;
    const ok = await createConversation(payload);
    window.dispatchEvent(new CustomEvent("helix-conversation-created", { detail: { ok } }));
  });
  return true;
}
