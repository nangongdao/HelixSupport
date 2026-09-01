/**
 * Helix Support — admin island pure models.
 *
 * Framework-agnostic row/readout builders (§43.6): verbatim logic from the
 * legacy app.js render* helpers and js/admin-report.js, with ctx reads
 * replaced by parameters. Split out of admin-island.jsx when it crossed the
 * 400-line module limit; the island re-exports every name so the component
 * test's import surface is unchanged.
 */

import {
  PRIORITY_LABELS,
  REPORT_TYPE_LABELS,
  ROLE_LABELS,
  SCHEDULE_LABELS,
} from "./constants.js";

/** app.js formatTime — subscription "上次" timestamps. */
export function formatTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

export function quotaReadoutRows(quota) {
  const storageMb = quota.storage_quota_bytes != null
    ? Math.round(quota.storage_quota_bytes / (1024 * 1024))
    : "—";
  return [
    ["租户", quota.name || quota.tenant_id],
    ["会话配额", quota.conversation_quota ?? "—"],
    ["存储配额", `${storageMb} MB`],
    ["每日 turn 预算", quota.daily_turn_budget ?? "—"],
    ["允许模型", (quota.allowed_models || []).join(", ") || "全部"],
  ];
}

export function memberRowModel(member, selfActor = "") {
  return {
    actorId: member.actor_id,
    role: member.role,
    roleLabel: ROLE_LABELS[member.role] || member.role,
    isSelf: member.actor_id === selfActor,
    deactivated: member.status === "deactivated",
  };
}

export function activeWebhookOptions(webhooks) {
  return (Array.isArray(webhooks) ? webhooks : []).filter(
    (hook) => hook.status === "active",
  );
}

export function subscriptionRowModel(sub) {
  return {
    id: sub.id,
    title: `${REPORT_TYPE_LABELS[sub.report_type] || sub.report_type} · ${SCHEDULE_LABELS[sub.schedule] || sub.schedule}`,
    meta: `窗口 ${String(sub.window_days)} 天 · ${sub.webhook_endpoint_id} · 上次 ${sub.last_run_at ? formatTime(sub.last_run_at) : "未运行"}`,
    active: Boolean(sub.active),
  };
}

export function slaPolicyLabel(policy) {
  const priority = policy.priority
    ? PRIORITY_LABELS[policy.priority] || policy.priority
    : "默认";
  const channel = policy.channel ? `渠道 ${policy.channel}` : "全渠道";
  return `${priority} · ${channel}`;
}

export function routingRuleLabel(rule) {
  const parts = [];
  if (rule.intent) parts.push(`意图 ${rule.intent}`);
  if (rule.label) parts.push(`标签 ${rule.label}`);
  if (rule.channel) parts.push(`渠道 ${rule.channel}`);
  return parts.length ? parts.join(" · ") : "全部会话";
}

export function routingRuleMeta(rule, groups) {
  const groupName = (groups || []).find((group) => group.id === rule.group_id)?.name || rule.group_id;
  return `→ ${groupName} · 优先级 ${rule.priority}`;
}

export function csatModel(data = {}) {
  const days = Array.isArray(data.per_day) ? data.per_day : [];
  const total = Math.round(Number(data.total || 0));
  // 有样本才有均值/好评率;空态用 — 而非 0.00(客户不可能打 0 分,W2)。
  const avg = total > 0 ? `${Number(data.avg_rating || 0).toFixed(2)} / 5` : "— / 5";
  const pct = total > 0 ? `${Math.round(Number(data.positive_rate || 0) * 100)}%` : "—";
  return {
    rows: [
      ["累计样本数", total],
      ["累计平均分", avg],
      ["累计好评率", pct],
    ],
    trend: days.map((d) => ({
      date: d.date,
      meta: `${d.count} 份 · 平均 ${Number(d.avg_rating || 0).toFixed(2)}`,
    })),
  };
}

/** admin-report.js exportReportCsv 的下载 URL(日期窗口逐字对齐)。 */
export function reportExportUrl(reportType, windowDays, now = new Date()) {
  const days = Math.min(30, Math.max(1, Number(windowDays || 7)));
  const from = new Date(now);
  from.setDate(now.getDate() - (days - 1));
  const fmt = (d) =>
    `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  return (
    `/api/admin/reports/${encodeURIComponent(reportType)}/export` +
    `?from=${fmt(from)}&to=${fmt(now)}`
  );
}
