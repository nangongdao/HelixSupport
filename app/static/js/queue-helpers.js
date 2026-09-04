/**
 * Helix Support — queue helpers (app.js <500 campaign).
 *
 * The queue list's shared query/derive helpers, extracted verbatim from
 * app.js: conversationQuery builds the live-filter URL query from the queue
 * controls, loadLabelCatalog is the 60s label-catalog cache, queueSignature
 * is the rich list-change signature (with claim/needs_response bits — distinct
 * from the lighter js/state.js variant). conversationQuery/loadLabelCatalog
 * arrive their els/state/api/queuePageSize through configure; queueSignature
 * is pure.
 */

let ctx = null;

/** Inject the legacy app.js singletons (els/state/api/queuePageSize). */
export function configure(deps) {
  ctx = deps;
}

/** Build the queue list query string from the current filter controls. */
export function conversationQuery() {
  const params = new URLSearchParams();
  const search = ctx.els.searchInput.value.trim();
  if (search) params.set("search", search);
  if (ctx.els.statusFilter.value) params.set("status", ctx.els.statusFilter.value);
  if (ctx.els.labelFilter.value) params.set("label", ctx.els.labelFilter.value);
  if (ctx.els.priorityFilter.value) params.set("priority", ctx.els.priorityFilter.value);
  if (ctx.els.channelFilter?.value) params.set("channel", ctx.els.channelFilter.value);
  if (ctx.els.sortFilter?.value) params.set("sort", ctx.els.sortFilter.value);
  const ownership = ctx.els.ownershipFilter.value;
  if (ownership === "mine") params.set("mine", "true");
  if (ownership === "unassigned") params.set("unassigned", "true");
  if (ownership === "unclaimed") params.set("unclaimed", "true");
  if (ownership === "claimed_by_me" && ctx.state.me?.actor_id) {
    params.set("claimed_by", ctx.state.me.actor_id);
  }
  if (ownership === "sla_breached") params.set("sla_breached", "true");
  if (ownership === "needs_response") params.set("needs_response", "true");
  params.set("limit", String(ctx.queuePageSize()));
  return params.toString();
}

/** Load the conversation-label catalog, cached for 60s unless forced. */
export async function loadLabelCatalog({ force = false } = {}) {
  if (!force && ctx.state.labelsLoadedAt && Date.now() - ctx.state.labelsLoadedAt < 60000 && ctx.state.labelCatalog.length) {
    return ctx.state.labelCatalog;
  }
  ctx.state.labelCatalog = await ctx.api("/api/conversation-labels");
  ctx.state.labelsLoadedAt = Date.now();
  return ctx.state.labelCatalog;
}

/** Rich signature of a conversation list (drives queue change detection). */
export function queueSignature(conversations) {
  return conversations
    .map(
      (item) =>
        `${item.id}:${item.version || 0}:${item.updated_at || ""}:${item.status}:${item.claim_active ? 1 : 0}:${item.needs_response ? 1 : 0}`,
    )
    .join("|");
}

export default { configure, conversationQuery, loadLabelCatalog, queueSignature };