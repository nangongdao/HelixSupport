/**
 * Helix Support — tenant admin quota/member/webhook CRUD (app.js <500
 * campaign slice 25).
 *
 * The admin page's quota/member/webhook domain: legacy renders (quota
 * readout, member roster with the Phase 32.1 self-guards, webhook list),
 * the form-write flows and the admin-island bridge handlers (each reports
 * helix-admin-saved {ok, domains} so the island refetches exactly the
 * queries a write touched). Extracted from the legacy app.js verbatim;
 * loadAdminView's report/SLA/routing/csat loaders and the render/derive
 * helpers that stay near their inputs arrive through configure.
 *
 * Pure admin helpers (roster normalization, report subscriptions, SLA
 * policies, routing rules) remain in admin-report.js.
 */

let ctx = null;

/** Inject the legacy app.js singletons (state/els/api/helpers). */
export function configure(deps) {
  ctx = deps;
}

export const WEBHOOK_EVENTS = [
  ["conversation.created", "会话创建"],
  ["conversation.escalated", "升级人工"],
  ["conversation.resolved", "会话解决"],
  ["conversation.sla_breached", "SLA 违约"],
  ["conversation.sla_impending", "SLA 临近"],
  ["report.generated", "报表生成"],
];

export function canManage() {
  // The page includes Webhook/report/routing operations whose backend
  // contract requires admin:manage. A tenant:manage-only principal may use
  // the API's quota/member routes directly but must see the page denial
  // rather than enter a partially forbidden workspace.
  return ctx.state.me?.permissions?.includes("admin:manage") === true;
}

export function adminTenantId() {
  return ctx.state.me?.tenant_id || ctx.TENANT;
}

export function renderWebhookEventCheckboxes() {
  if (!ctx.els.webhookEvents) return;
  ctx.els.webhookEvents.innerHTML = WEBHOOK_EVENTS
    .map(([event, label]) => `<label><input type="checkbox" value="${ctx.escapeHtml(event)}" />${ctx.escapeHtml(label)}</label>`)
    .join("");
}

export function renderQuota(quota) {
  if (!ctx.els.quotaReadout) return;
  const storageMb = quota.storage_quota_bytes != null
    ? Math.round(quota.storage_quota_bytes / (1024 * 1024))
    : "—";
  ctx.els.quotaReadout.innerHTML = `
    <dt>租户</dt><dd>${ctx.escapeHtml(quota.name || quota.tenant_id)}</dd>
    <dt>会话配额</dt><dd>${quota.conversation_quota ?? "—"}</dd>
    <dt>存储配额</dt><dd>${storageMb} MB</dd>
    <dt>每日 turn 预算</dt><dd>${quota.daily_turn_budget ?? "—"}</dd>
    <dt>允许模型</dt><dd>${(quota.allowed_models || []).join(", ") || "全部"}</dd>`;
}

export function renderMembers(members) {
  if (!ctx.els.memberList) return;
  if (!members.length) {
    ctx.els.memberList.innerHTML = '<li class="admin-empty">暂无成员</li>';
    return;
  }
  const selfActor = ctx.state.me?.actor_id || "";
  ctx.els.memberList.innerHTML = members
    .map((member) => {
      const isSelf = member.actor_id === selfActor;
      // Phase 32.1 audit: self-deactivation guard mirrors the backend. The
      // role select and deactivate button are disabled for the current
      // admin so the last tenant:manage holder cannot lock the tenant.
      const selfGuard = isSelf ? " disabled" : "";
      const selfHint = isSelf ? "（你）" : "";
      return `
      <li class="admin-member">
        <span class="admin-member-actor">${ctx.escapeHtml(member.actor_id)}${selfHint}</span>
        <span class="admin-member-role">${ctx.escapeHtml(ctx.roleLabels[member.role] || member.role)}</span>
        <span class="admin-member-actions">
          <select class="admin-ghost-button member-role-select" data-actor="${ctx.escapeHtml(member.actor_id)}" aria-label="变更角色"${selfGuard}>
            ${Object.entries(ctx.roleLabels).map(([value, label]) => `<option value="${value}"${value === member.role ? " selected" : ""}>${label}</option>`).join("")}
          </select>
          ${member.status !== "deactivated"
            ? `<button type="button" class="admin-ghost-button member-deactivate" data-actor="${ctx.escapeHtml(member.actor_id)}"${selfGuard}>停用</button>`
            : '<span class="admin-member-role">已停用</span>'}
        </span>
      </li>`;
    })
    .join("");
}

export function renderWebhooks(webhooks) {
  if (!ctx.els.webhookList) return;
  if (!webhooks.length) {
    ctx.els.webhookList.innerHTML = '<li class="admin-empty">暂无 Webhook</li>';
    return;
  }
  ctx.els.webhookList.innerHTML = webhooks
    .map((hook) => `
      <li class="admin-webhook">
        <span class="admin-webhook-url" title="${ctx.escapeHtml(hook.url)}">${ctx.escapeHtml(hook.url)}</span>
        <span class="admin-webhook-events">${ctx.escapeHtml(hook.events.join(" · "))}</span>
        <button type="button" class="admin-ghost-button webhook-delete" data-id="${ctx.escapeHtml(hook.id)}">删除</button>
      </li>`)
    .join("");
}

export async function loadAdminView() {
  if (!ctx.els.adminView || !ctx.canManage()) {
    if (ctx.els.adminDenied) ctx.els.adminDenied.hidden = false;
    if (ctx.els.adminContent) ctx.els.adminContent.hidden = true;
    return;
  }
  if (ctx.els.adminDenied) ctx.els.adminDenied.hidden = true;
  if (ctx.els.adminContent) ctx.els.adminContent.hidden = false;
  // Island mode: the admin island owns the whole card grid and fetches via
  // react-query. Fetching here would only fill the yielded legacy cards and
  // double every request, so hand the refresh over. The denied/content
  // toggling above stays legacy on purpose — the denial panel lives outside
  // the yielded cards and the island renders nothing for non-admins.
  if (window.__HELIX_ISLAND_MODE__) {
    window.dispatchEvent(new CustomEvent("helix-admin-refresh", { detail: { force: false } }));
    return;
  }
  const tenantId = adminTenantId();
  try {
    const [quota, members, webhooks] = await Promise.all([
      ctx.api(`/api/admin/tenants/${encodeURIComponent(tenantId)}/quota`),
      ctx.api(`/api/admin/tenants/${encodeURIComponent(tenantId)}/members`),
      ctx.api("/api/webhooks"),
    ]);
    renderQuota(quota);
    renderMembers(Array.isArray(members) ? members : []);
    renderWebhooks(Array.isArray(webhooks) ? webhooks : []);
    ctx.actions.renderReportWebhookOptions(webhooks);
    await ctx.actions.loadReportSubscriptions();
    // 先填充 agent-groups cache 再渲染路由规则,否则首屏每条规则
    // group_id 落入 id fallback(组名不可解析)。
    await ctx.actions.loadRuleGroups();
    await ctx.actions.loadSlaPolicies();
    await ctx.actions.loadRoutingRules();
    await ctx.actions.loadCsatSummary();
  } catch (error) {
    ctx.showToast(`管理数据加载失败：${error.message || error}`, true);
  }
}

export async function saveQuota(event) {
  event.preventDefault();
  const body = {};
  if (ctx.els.quotaConversations?.value !== "") body.conversation_quota = Number(ctx.els.quotaConversations.value);
  if (ctx.els.quotaStorageMb?.value !== "") body.storage_quota_bytes = Number(ctx.els.quotaStorageMb.value) * 1024 * 1024;
  if (!Object.keys(body).length) return;
  try {
    const quota = await ctx.api(`/api/admin/tenants/${encodeURIComponent(adminTenantId())}/quota`, {
      method: "PUT",
      body: JSON.stringify(body),
    });
    renderQuota(quota);
    if (ctx.els.quotaConversations) ctx.els.quotaConversations.value = "";
    if (ctx.els.quotaStorageMb) ctx.els.quotaStorageMb.value = "";
    ctx.showToast("配额已更新");
  } catch (error) {
    ctx.showToast(`配额保存失败：${error.message || error}`, true);
  }
}

export async function inviteMember(event) {
  event.preventDefault();
  const actorId = ctx.els.memberActorId?.value.trim();
  if (!actorId) return;
  try {
    await ctx.api(`/api/admin/tenants/${encodeURIComponent(adminTenantId())}/members`, {
      method: "POST",
      body: JSON.stringify({ actor_id: actorId, role: ctx.els.memberRole?.value || "operator" }),
    });
    if (ctx.els.memberActorId) ctx.els.memberActorId.value = "";
    await loadAdminView();
    ctx.showToast("成员已邀请");
  } catch (error) {
    ctx.showToast(`邀请失败：${error.message || error}`, true);
  }
}

export async function changeMemberRole(actorId, role) {
  // Phase 32.1 audit (client-side self-guard): the backend rejects demoting
  // yourself; surface the message before the round-trip so the UX is clear.
  if (actorId === (ctx.state.me?.actor_id || "") && role !== "admin") {
    ctx.showToast("不能把自己的角色降级——至少保留一位管理员", true);
    return;
  }
  try {
    await ctx.api(`/api/admin/tenants/${encodeURIComponent(adminTenantId())}/members/${encodeURIComponent(actorId)}`, {
      method: "PATCH",
      body: JSON.stringify({ role }),
    });
    await loadAdminView();
  } catch (error) {
    ctx.showToast(`角色变更失败：${error.message || error}`, true);
  }
}

export async function deactivateMember(actorId) {
  // Phase 32.1 audit (client-side self-guard): the backend rejects this.
  if (actorId === (ctx.state.me?.actor_id || "")) {
    ctx.showToast("不能停用自己——请先指派另一位管理员", true);
    return;
  }
  try {
    await ctx.api(`/api/admin/tenants/${encodeURIComponent(adminTenantId())}/members/${encodeURIComponent(actorId)}/deactivate`, {
      method: "POST",
      body: "{}",
    });
    await loadAdminView();
  } catch (error) {
    ctx.showToast(`停用失败：${error.message || error}`, true);
  }
}

export async function registerWebhook(event) {
  event.preventDefault();
  const url = ctx.els.webhookUrl?.value.trim();
  const secret = ctx.els.webhookSecret?.value.trim();
  const events = Array.from(ctx.els.webhookEvents?.querySelectorAll('input[type="checkbox"]:checked') || [])
    .map((input) => input.value);
  if (!url || !secret || !events.length) {
    ctx.showToast("请填写 URL、密钥并至少选择一个事件", true);
    return;
  }
  try {
    await ctx.api("/api/webhooks", {
      method: "POST",
      body: JSON.stringify({ url, events, secret }),
    });
    if (ctx.els.webhookUrl) ctx.els.webhookUrl.value = "";
    if (ctx.els.webhookSecret) ctx.els.webhookSecret.value = "";
    if (ctx.els.webhookEvents) {
      ctx.els.webhookEvents.querySelectorAll('input[type="checkbox"]').forEach((input) => { input.checked = false; });
    }
    await loadAdminView();
    ctx.showToast("Webhook 已注册");
  } catch (error) {
    ctx.showToast(`Webhook 注册失败：${error.message || error}`, true);
  }
}

export async function deleteWebhook(id) {
  // Phase 32.1 audit: destructive action — confirm before sending. Avoids
  // accidental double-clicks wiping an active delivery target.
  if (!window.confirm("确认删除该 Webhook 端点？已注册的待投递事件将进入死信。")) return;
  try {
    await ctx.api(`/api/webhooks/${encodeURIComponent(id)}`, { method: "DELETE" });
    await loadAdminView();
  } catch (error) {
    ctx.showToast(`删除失败：${error.message || error}`, true);
  }
}

function dispatchAdminSaved(ok, domains) {
  window.dispatchEvent(new CustomEvent("helix-admin-saved", { detail: { ok, domains } }));
}

export async function saveQuotaFromIsland({ conversation_quota: conversations, storage_quota_bytes: storage } = {}) {
  const body = {};
  if (conversations != null) body.conversation_quota = Number(conversations);
  if (storage != null) body.storage_quota_bytes = Number(storage);
  if (!Object.keys(body).length) return;
  let ok = false;
  try {
    await ctx.api(`/api/admin/tenants/${encodeURIComponent(adminTenantId())}/quota`, {
      method: "PUT",
      body: JSON.stringify(body),
    });
    ok = true;
    ctx.showToast("配额已更新");
  } catch (error) {
    ctx.showToast(`配额保存失败：${error.message || error}`, true);
  } finally {
    dispatchAdminSaved(ok, ["quota"]);
  }
}

export async function inviteMemberFromIsland({ actorId, role } = {}) {
  if (!actorId) return;
  let ok = false;
  try {
    await ctx.api(`/api/admin/tenants/${encodeURIComponent(adminTenantId())}/members`, {
      method: "POST",
      body: JSON.stringify({ actor_id: actorId, role: role || "operator" }),
    });
    ok = true;
    ctx.showToast("成员已邀请");
  } catch (error) {
    ctx.showToast(`邀请失败：${error.message || error}`, true);
  } finally {
    dispatchAdminSaved(ok, ["members"]);
  }
}

export async function changeMemberRoleFromIsland({ actorId, role } = {}) {
  if (!actorId || !role) return;
  // Phase 32.1 audit (client-side self-guard): the backend rejects demoting
  // yourself; surface the message before the round-trip so the UX is clear.
  if (actorId === (ctx.state.me?.actor_id || "") && role !== "admin") {
    ctx.showToast("不能把自己的角色降级——至少保留一位管理员", true);
    return;
  }
  let ok = false;
  try {
    await ctx.api(`/api/admin/tenants/${encodeURIComponent(adminTenantId())}/members/${encodeURIComponent(actorId)}`, {
      method: "PATCH",
      body: JSON.stringify({ role }),
    });
    ok = true;
  } catch (error) {
    ctx.showToast(`角色变更失败：${error.message || error}`, true);
  } finally {
    dispatchAdminSaved(ok, ["members"]);
  }
}

export async function deactivateMemberFromIsland({ actorId } = {}) {
  if (!actorId) return;
  // Phase 32.1 audit (client-side self-guard): the backend rejects this.
  if (actorId === (ctx.state.me?.actor_id || "")) {
    ctx.showToast("不能停用自己——请先指派另一位管理员", true);
    return;
  }
  let ok = false;
  try {
    await ctx.api(`/api/admin/tenants/${encodeURIComponent(adminTenantId())}/members/${encodeURIComponent(actorId)}/deactivate`, {
      method: "POST",
      body: "{}",
    });
    ok = true;
  } catch (error) {
    ctx.showToast(`停用失败：${error.message || error}`, true);
  } finally {
    dispatchAdminSaved(ok, ["members"]);
  }
}

export async function registerWebhookFromIsland({ url, secret, events } = {}) {
  const hookUrl = (url || "").trim();
  const hookSecret = (secret || "").trim();
  const hookEvents = Array.isArray(events) ? events : [];
  if (!hookUrl || !hookSecret || !hookEvents.length) {
    ctx.showToast("请填写 URL、密钥并至少选择一个事件", true);
    return;
  }
  let ok = false;
  try {
    await ctx.api("/api/webhooks", {
      method: "POST",
      body: JSON.stringify({ url: hookUrl, events: hookEvents, secret: hookSecret }),
    });
    ok = true;
    ctx.showToast("Webhook 已注册");
  } catch (error) {
    ctx.showToast(`Webhook 注册失败：${error.message || error}`, true);
  } finally {
    // 报表订阅的下拉选项来自 active webhooks — 两者一起失效。
    dispatchAdminSaved(ok, ["webhooks", "subscriptions"]);
  }
}

export async function deleteWebhookFromIsland({ id } = {}) {
  if (!id) return;
  // Phase 32.1 audit: the confirm() prompt stays in legacy, exactly like
  // the knowledge island's retire flow.
  if (!window.confirm("确认删除该 Webhook 端点？已注册的待投递事件将进入死信。")) return;
  let ok = false;
  try {
    await ctx.api(`/api/webhooks/${encodeURIComponent(id)}`, { method: "DELETE" });
    ok = true;
  } catch (error) {
    ctx.showToast(`删除失败：${error.message || error}`, true);
  } finally {
    dispatchAdminSaved(ok, ["webhooks", "subscriptions"]);
  }
}

export default {
  WEBHOOK_EVENTS, adminTenantId, renderWebhookEventCheckboxes, renderQuota,
  renderMembers, renderWebhooks, loadAdminView, saveQuota, inviteMember,
  changeMemberRole, deactivateMember, registerWebhook, deleteWebhook,
  saveQuotaFromIsland, inviteMemberFromIsland, changeMemberRoleFromIsland,
  deactivateMemberFromIsland, registerWebhookFromIsland, deleteWebhookFromIsland,
};
