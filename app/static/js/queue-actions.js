/**
 * Helix Support — queue pagination & bulk actions (app.js <500 slice 24).
 *
 * The queue's write/pagination operations: upward-less keyset pagination
 * (loadMoreConversations with the query-key staleness guard) and the bulk
 * action lifecycle (applyBulkAction — shared by the legacy toolbar form and
 * the island's helix-queue-bulk-apply bridge). Extracted from the legacy
 * app.js verbatim; renderQueue/renderBulkToolbar and the refresh entry
 * arrive through configure because they belong to the render/refresh
 * domains. The island bridges (helix-queue-load-more/-bulk/-bulk-apply/
 * -bulk-clear) route the desktop shell's controls to the same lifecycle.
 */

let ctx = null;

/** Inject the legacy app.js singletons (state/els/api/helpers). */
export function configure(deps) {
  ctx = deps;
}

export async function loadMoreConversations() {
  if (!ctx.state.queueHasMore || !ctx.state.queueCursor || ctx.state.queueLoadingMore) return;
  const query = new URLSearchParams(ctx.actions.conversationQuery());
  const queryKey = `${query.get("search") || ""}|${query.get("status") || ""}|${query.get("label") || ""}|${query.get("priority") || ""}|${ctx.els.ownershipFilter.value}|${ctx.els.channelFilter?.value || ""}|${ctx.els.sortFilter?.value || "priority"}`;
  query.set("cursor", ctx.state.queueCursor);
  ctx.state.queueLoadingMore = true;
  ctx.actions.renderQueue();
  try {
    const page = await ctx.apiWithHeaders(`/api/conversations?${query.toString()}`);
    const activeQueryKey = `${ctx.els.searchInput.value.trim()}|${ctx.els.statusFilter.value}|${ctx.els.labelFilter.value}|${ctx.els.priorityFilter.value}|${ctx.els.ownershipFilter.value}|${ctx.els.channelFilter?.value || ""}|${ctx.els.sortFilter?.value || "priority"}`;
    if (activeQueryKey !== queryKey) return;
    const existing = new Set(ctx.state.conversations.map((conversation) => conversation.id));
    ctx.state.conversations = [
      ...ctx.state.conversations,
      ...page.data.filter((conversation) => !existing.has(conversation.id)),
    ];
    ctx.state.queueHasMore = page.response.headers.get("X-Has-More") === "true";
    ctx.state.queueCursor = page.response.headers.get("X-Next-Cursor");
    ctx.actions.renderQueue();
  } catch (error) {
    ctx.showToast(error.message, true);
  } finally {
    ctx.state.queueLoadingMore = false;
    ctx.actions.renderQueue();
  }
}

/**
 * Bulk action lifecycle — shared by the legacy toolbar form and the
 * island's helix-queue-bulk-apply bridge. `source` carries the island's
 * {action, labels}; without it the values come from the legacy toolbar
 * inputs (browser dual-track).
 */
export async function applyBulkAction(source = null) {
  const conversationIds = [...ctx.state.bulkSelected];
  if (!conversationIds.length) return;
  const selectedAction = source ? source.action : ctx.els.bulkAction.value;
  const payload = { conversation_ids: conversationIds };
  if (selectedAction === "priority-high" || selectedAction === "priority-normal") {
    payload.action = "set_priority";
    payload.priority = selectedAction === "priority-high" ? "high" : "normal";
  } else if (selectedAction === "claim" || selectedAction === "release") {
    payload.action = selectedAction;
  } else {
    const labels = source
      ? source.labels
      : ctx.els.bulkLabelInput.value
          .split(/[,，]/)
          .map((label) => label.trim())
          .filter(Boolean);
    if (!labels.length) {
      ctx.showToast("请输入标签", true);
      ctx.els.bulkLabelInput.focus();
      return;
    }
    payload.action = selectedAction === "add-label" ? "add_labels" : "remove_labels";
    payload.labels = labels;
  }
  ctx.setFormBusy(ctx.els.bulkToolbar, true);
  try {
    const result = await ctx.api("/api/conversations/bulk-actions", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    ctx.showToast(`已更新 ${result.updated} 个会话`);
    ctx.state.bulkSelected.clear();
    ctx.els.bulkLabelInput.value = "";
    if (["add-label", "remove-label"].includes(selectedAction)) ctx.state.labelsLoadedAt = 0;
    await ctx.refreshAll({ silent: true });
  } catch (error) {
    ctx.showToast(error.message, true);
  } finally {
    ctx.setFormBusy(ctx.els.bulkToolbar, false);
    ctx.actions.renderBulkToolbar();
  }
}

/** Bind the load-more / bulk controls and the queue island bridges (exactly
 * once, at boot). */
export function bindQueueActions() {
  if (!ctx?.els) return false;
  ctx.els.loadMore.addEventListener("click", loadMoreConversations);
  ctx.els.applyBulk.addEventListener("click", () => void applyBulkAction());
  // D3 bridge (queue island strip): the island's 加载更多 button dispatches
  // helix-queue-load-more; the pagination lifecycle (cursor, loading-more
  // guard, query-key staleness check) stays here.
  window.addEventListener("helix-queue-load-more", () => void loadMoreConversations());
  // D3 bridge: the React queue island dispatches "helix-queue-bulk" when a
  // row checkbox toggles (the legacy list is yielded in the desktop shell).
  // Mirror the legacy change handler so the bulk toolbar stays in sync.
  window.addEventListener("helix-queue-bulk", (event) => {
    const { id, on } = event.detail || {};
    if (!id) return;
    if (on) ctx.state.bulkSelected.add(id);
    else ctx.state.bulkSelected.delete(id);
    ctx.actions.renderBulkToolbar();
  });
  // D3 bridge (queue island bulk toolbar): apply/clear arrive with the
  // island's toolbar state; the bulk lifecycle (payload build, POST, toast,
  // selection reset, refresh) stays here and reports completion so the
  // island can release its busy state.
  window.addEventListener("helix-queue-bulk-apply", async (event) => {
    const { action, labels } = event.detail || {};
    if (!action) return;
    await applyBulkAction({ action, labels });
    window.dispatchEvent(new CustomEvent("helix-queue-bulk-applied"));
  });
  window.addEventListener("helix-queue-bulk-clear", () => {
    ctx.state.bulkSelected.clear();
    ctx.actions.renderQueue();
  });
  return true;
}

export default { loadMoreConversations, applyBulkAction, bindQueueActions };
