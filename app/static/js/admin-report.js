/**
 * Helix Support — admin report controller (ROADMAP §41.6 / ARC-001).
 *
 * Report subscriptions/export, SLA policies and automatic routing rules in
 * the admin view. Extracted from the legacy app.js; app.js keeps thin
 * delegating wrappers with identical names/signatures, so the running UI
 * behaviour is unchanged. app.js calls configure() once at load time.
 */

let ctx = null;

/** Inject the legacy app.js singletons (state/els/api/helpers). */
export function configure(deps) {
  ctx = deps;
}

const REPORT_TYPE_LABELS = { quality: "质量报表", usage: "使用量报表" };
const SCHEDULE_LABELS = { daily: "每日", weekly: "每周" };
let reportSubscriptionsCache = [];

export function renderReportWebhookOptions(webhooks) {
  if (!ctx.els.reportWebhook) return;
  const active = (Array.isArray(webhooks) ? webhooks : []).filter(
    (hook) => hook.status === "active",
  );
  ctx.els.reportWebhook.innerHTML = active.length
    ? active
        .map((hook) => `<option value="${ctx.escapeHtml(hook.id)}">${ctx.escapeHtml(hook.url)}</option>`)
        .join("")
    : '<option value="">暂无可用 Webhook（请先注册）</option>';
}

export async function loadReportSubscriptions() {
  if (!ctx.els.reportSubscriptionList) return;
  try {
    const subs = await ctx.api("/api/admin/report-subscriptions");
    renderReportSubscriptions(Array.isArray(subs) ? subs : []);
  } catch (error) {
    reportSubscriptionsCache = [];
    if (ctx.els.reportSubscriptionList) {
      ctx.els.reportSubscriptionList.innerHTML = `<li class="admin-empty">订阅加载失败：${ctx.escapeHtml(error.message || error)}</li>`;
    }
  }
}

export function renderReportSubscriptions(subs) {
  reportSubscriptionsCache = subs;
  if (!ctx.els.reportSubscriptionList) return;
  if (!subs.length) {
    ctx.els.reportSubscriptionList.innerHTML = '<li class="admin-empty">暂无报表订阅</li>';
    return;
  }
  ctx.els.reportSubscriptionList.innerHTML = subs
    .map((sub) => {
      const reportName = REPORT_TYPE_LABELS[sub.report_type] || sub.report_type;
      const scheduleName = SCHEDULE_LABELS[sub.schedule] || sub.schedule;
      const lastRun = sub.last_run_at ? ctx.formatTime(sub.last_run_at) : "未运行";
      return `<li class="admin-report-sub" data-id="${ctx.escapeHtml(sub.id)}">
        <span class="admin-report-sub-main">
          <span class="admin-report-sub-title">${ctx.escapeHtml(reportName)} · ${ctx.escapeHtml(scheduleName)}</span>
          <span class="admin-report-sub-meta">窗口 ${ctx.escapeHtml(String(sub.window_days))} 天 · ${ctx.escapeHtml(sub.webhook_endpoint_id)} · 上次 ${ctx.escapeHtml(lastRun)}</span>
        </span>
        <span class="admin-member-actions">
          <span class="status-pill${sub.active ? "" : " is-ticket-closed"}">${sub.active ? "启用" : "停用"}</span>
          <button type="button" class="admin-ghost-button report-sub-toggle" data-id="${ctx.escapeHtml(sub.id)}">${sub.active ? "停用" : "启用"}</button>
          <button type="button" class="admin-ghost-button report-sub-delete" data-id="${ctx.escapeHtml(sub.id)}">删除</button>
        </span>
      </li>`;
    })
    .join("");
}

export async function createReportSubscription(event) {
  event.preventDefault();
  const webhookId = ctx.els.reportWebhook?.value;
  if (!webhookId) {
    ctx.showToast("请先选择 Webhook 端点", true);
    return;
  }
  const body = {
    report_type: ctx.els.reportType?.value || "quality",
    schedule: ctx.els.reportSchedule?.value || "daily",
    window_days: Number(ctx.els.reportWindowDays?.value || 7),
    webhook_endpoint_id: webhookId,
  };
  try {
    await ctx.api("/api/admin/report-subscriptions", {
      method: "POST",
      body: JSON.stringify(body),
    });
    await loadReportSubscriptions();
    ctx.showToast("报表订阅已创建");
  } catch (error) {
    ctx.showToast(`创建订阅失败：${error.message || error}`, true);
  }
}

export async function toggleReportSubscription(id) {
  const sub = reportSubscriptionsCache.find((entry) => entry.id === id);
  if (!sub) return;
  try {
    await ctx.api(`/api/admin/report-subscriptions/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify({ active: !sub.active }),
    });
    await loadReportSubscriptions();
  } catch (error) {
    ctx.showToast(`订阅状态变更失败：${error.message || error}`, true);
  }
}

export async function deleteReportSubscription(id) {
  try {
    await ctx.api(`/api/admin/report-subscriptions/${encodeURIComponent(id)}`, {
      method: "DELETE",
    });
    await loadReportSubscriptions();
    ctx.showToast("订阅已删除");
  } catch (error) {
    ctx.showToast(`删除订阅失败：${error.message || error}`, true);
  }
}

export async function generateReportPreview(event) {
  event.preventDefault();
  if (ctx.els.reportPreview) ctx.els.reportPreview.hidden = true;
  const reportType = ctx.els.reportGenerateType?.value || "quality";
  const windowDays = Number(ctx.els.reportGenerateWindow?.value || 7);
  try {
    const report = await ctx.api("/api/admin/reports/generate", {
      method: "POST",
      body: JSON.stringify({ report_type: reportType, window_days: windowDays }),
    });
    if (ctx.els.reportPreview) {
      const rows = Array.isArray(report.rows) ? report.rows : [];
      const header = `${REPORT_TYPE_LABELS[reportType] || reportType} ${report.from_date} → ${report.to_date}：${rows.length} 行`;
      ctx.els.reportPreview.textContent = rows.length
        ? `${header}\n${rows.slice(0, 5).map((row) => JSON.stringify(row)).join("\n")}`
        : `${header}\n（窗口内暂无数据）`;
      ctx.els.reportPreview.hidden = false;
    }
  } catch (error) {
    ctx.showToast(`报表生成失败：${error.message || error}`, true);
  }
}

export function exportReportCsv(event) {
  event.preventDefault();
  const reportType = ctx.els.reportGenerateType?.value || "quality";
  const windowDays = Math.min(30, Math.max(1, Number(ctx.els.reportGenerateWindow?.value || 7)));
  const today = new Date();
  const from = new Date(today);
  from.setDate(today.getDate() - (windowDays - 1));
  const fmt = (d) =>
    `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  // 下载由带鉴权 cookie 的导航触发(attachment disposition)。
  window.location.href =
    `/api/admin/reports/${encodeURIComponent(reportType)}/export` +
    `?from=${fmt(from)}&to=${fmt(today)}`;
}

const PRIORITY_LABELS = { normal: "普通", high: "高优" };
let slaPoliciesCache = [];
let agentGroupsCache = [];
let routingRulesCache = [];

export function slaPolicyLabel(policy) {
  const priority = policy.priority
    ? PRIORITY_LABELS[policy.priority] || policy.priority
    : "默认";
  const channel = policy.channel ? `渠道 ${policy.channel}` : "全渠道";
  return `${priority} · ${channel}`;
}

export function renderSlaPolicies(policies) {
  slaPoliciesCache = policies;
  if (!ctx.els.slaPolicyList) return;
  if (!policies.length) {
    ctx.els.slaPolicyList.innerHTML = '<li class="admin-empty">暂无 SLA 策略（走全局默认）</li>';
    return;
  }
  ctx.els.slaPolicyList.innerHTML = policies
    .map(
      (policy) => `
      <li class="sla-rule-row" data-id="${ctx.escapeHtml(policy.id)}">
        <span class="sla-rule-main">
          <span class="sla-rule-title">${ctx.escapeHtml(slaPolicyLabel(policy))}</span>
          <span class="sla-rule-meta">首响 ${policy.first_response_minutes}min · 解决 ${policy.resolve_minutes}min</span>
        </span>
        <span class="admin-member-actions">
          <button type="button" class="admin-ghost-button sla-policy-fill" data-id="${ctx.escapeHtml(policy.id)}" title="填入表单编辑">编辑</button>
        </span>
      </li>`,
    )
    .join("");
}

export async function loadSlaPolicies() {
  if (!ctx.els.slaPolicyList) return;
  try {
    const policies = await ctx.api("/api/admin/sla-policies");
    renderSlaPolicies(Array.isArray(policies) ? policies : []);
  } catch (error) {
    slaPoliciesCache = [];
    if (ctx.els.slaPolicyList) {
      ctx.els.slaPolicyList.innerHTML = `<li class="admin-empty">SLA 策略加载失败：${ctx.escapeHtml(error.message || error)}</li>`;
    }
  }
}

export function fillSlaPolicyForm(id) {
  const policy = slaPoliciesCache.find((entry) => entry.id === id);
  if (!policy) return;
  if (ctx.els.slaPriority) ctx.els.slaPriority.value = policy.priority || "";
  if (ctx.els.slaChannel) ctx.els.slaChannel.value = policy.channel || "";
  if (ctx.els.slaFirstResponse) ctx.els.slaFirstResponse.value = String(policy.first_response_minutes);
  if (ctx.els.slaResolve) ctx.els.slaResolve.value = String(policy.resolve_minutes);
}

export async function saveSlaPolicy(event) {
  event.preventDefault();
  const firstResponse = Number(ctx.els.slaFirstResponse?.value || 0);
  const resolveMinutes = Number(ctx.els.slaResolve?.value || 0);
  if (!firstResponse || !resolveMinutes) {
    ctx.showToast("请填写首响与解决时限", true);
    return;
  }
  const body = {
    priority: ctx.els.slaPriority?.value || null,
    channel: (ctx.els.slaChannel?.value || "").trim() || null,
    first_response_minutes: firstResponse,
    resolve_minutes: resolveMinutes,
  };
  try {
    await ctx.api("/api/admin/sla-policies", {
      method: "PUT",
      body: JSON.stringify(body),
    });
    await loadSlaPolicies();
    ctx.showToast("SLA 策略已保存");
  } catch (error) {
    ctx.showToast(`SLA 策略保存失败：${error.message || error}`, true);
  }
}

export function renderRuleGroups(groups) {
  agentGroupsCache = groups;
  if (!ctx.els.ruleGroup) return;
  ctx.els.ruleGroup.innerHTML = groups.length
    ? groups
        .map((group) => `<option value="${ctx.escapeHtml(group.id)}">${ctx.escapeHtml(group.name)}</option>`)
        .join("")
    : '<option value="">暂无坐席组（请先创建）</option>';
}

export async function loadRuleGroups() {
  try {
    const groups = await ctx.api("/api/admin/agent-groups");
    renderRuleGroups(Array.isArray(groups) ? groups : []);
  } catch (error) {
    agentGroupsCache = [];
    if (ctx.els.ruleGroup) {
      ctx.els.ruleGroup.innerHTML = `<option value="">坐席组加载失败（${ctx.escapeHtml(error.message || error)}）</option>`;
    }
  }
}

export function routingRuleLabel(rule) {
  const parts = [];
  if (rule.intent) parts.push(`意图 ${rule.intent}`);
  if (rule.label) parts.push(`标签 ${rule.label}`);
  if (rule.channel) parts.push(`渠道 ${rule.channel}`);
  return parts.length ? parts.join(" · ") : "全部会话";
}

export function renderRoutingRules(rules) {
  routingRulesCache = rules;
  if (!ctx.els.routingRuleList) return;
  if (!rules.length) {
    ctx.els.routingRuleList.innerHTML = '<li class="admin-empty">暂无路由规则</li>';
    return;
  }
  ctx.els.routingRuleList.innerHTML = rules
    .map((rule) => {
      const groupName = agentGroupsCache.find((group) => group.id === rule.group_id)?.name || rule.group_id;
      return `<li class="routing-rule-row" data-id="${ctx.escapeHtml(rule.id)}">
        <span class="routing-rule-main">
          <span class="routing-rule-title">${ctx.escapeHtml(routingRuleLabel(rule))}</span>
          <span class="routing-rule-meta">→ ${ctx.escapeHtml(groupName)} · 优先级 ${rule.priority}</span>
        </span>
        <span class="admin-member-actions">
          <button type="button" class="admin-ghost-button routing-rule-delete" data-id="${ctx.escapeHtml(rule.id)}">删除</button>
        </span>
      </li>`;
    })
    .join("");
}

export async function loadRoutingRules() {
  if (!ctx.els.routingRuleList) return;
  try {
    const rules = await ctx.api("/api/admin/routing-rules");
    renderRoutingRules(Array.isArray(rules) ? rules : []);
  } catch (error) {
    routingRulesCache = [];
    if (ctx.els.routingRuleList) {
      ctx.els.routingRuleList.innerHTML = `<li class="admin-empty">路由规则加载失败：${ctx.escapeHtml(error.message || error)}</li>`;
    }
  }
}

export async function createRoutingRule(event) {
  event.preventDefault();
  const groupId = ctx.els.ruleGroup?.value;
  if (!groupId) {
    ctx.showToast("请先选择分配组", true);
    return;
  }
  const body = { group_id: groupId, priority: Number(ctx.els.rulePriority?.value || 0) };
  for (const [key, input] of [
    ["intent", ctx.els.ruleIntent],
    ["label", ctx.els.ruleLabel],
    ["channel", ctx.els.ruleChannel],
  ]) {
    const trimmed = (input?.value || "").trim();
    if (trimmed) body[key] = trimmed;
  }
  try {
    await ctx.api("/api/admin/routing-rules", {
      method: "POST",
      body: JSON.stringify(body),
    });
    if (ctx.els.ruleIntent) ctx.els.ruleIntent.value = "";
    if (ctx.els.ruleLabel) ctx.els.ruleLabel.value = "";
    if (ctx.els.ruleChannel) ctx.els.ruleChannel.value = "";
    await loadRoutingRules();
    ctx.showToast("路由规则已添加");
  } catch (error) {
    ctx.showToast(`路由规则添加失败：${error.message || error}`, true);
  }
}

export async function deleteRoutingRule(id) {
  try {
    await ctx.api(`/api/admin/routing-rules/${encodeURIComponent(id)}`, {
      method: "DELETE",
    });
    await loadRoutingRules();
    ctx.showToast("路由规则已删除");
  } catch (error) {
    ctx.showToast(`路由规则删除失败：${error.message || error}`, true);
  }
}

/**
 * Bind the admin report/SLA/routing DOM (exactly once, at app.js load).
 */
export function bindAdminReports() {
  if (!ctx?.els) return false;
  if (ctx.els.reportSubscriptionForm) {
    ctx.els.reportSubscriptionForm.addEventListener("submit", (event) => void createReportSubscription(event));
  }
  if (ctx.els.reportSubscriptionList) {
    ctx.els.reportSubscriptionList.addEventListener("click", (event) => {
      const toggle = event.target.closest(".report-sub-toggle");
      if (toggle) {
        void toggleReportSubscription(toggle.dataset.id);
        return;
      }
      const remove = event.target.closest(".report-sub-delete");
      if (remove) void deleteReportSubscription(remove.dataset.id);
    });
  }
  if (ctx.els.reportGenerateForm) {
    ctx.els.reportGenerateForm.addEventListener("submit", (event) => {
      const mode = event.submitter?.value;
      if (mode === "export") exportReportCsv(event);
      else void generateReportPreview(event);
    });
  }
  if (ctx.els.slaPolicyForm) {
    ctx.els.slaPolicyForm.addEventListener("submit", (event) => void saveSlaPolicy(event));
  }
  if (ctx.els.slaPolicyList) {
    ctx.els.slaPolicyList.addEventListener("click", (event) => {
      const fill = event.target.closest(".sla-policy-fill");
      if (fill) fillSlaPolicyForm(fill.dataset.id);
    });
  }
  if (ctx.els.routingRuleForm) {
    ctx.els.routingRuleForm.addEventListener("submit", (event) => void createRoutingRule(event));
  }
  if (ctx.els.routingRuleList) {
    ctx.els.routingRuleList.addEventListener("click", (event) => {
      const remove = event.target.closest(".routing-rule-delete");
      if (remove) void deleteRoutingRule(remove.dataset.id);
    });
  }
  return true;
}