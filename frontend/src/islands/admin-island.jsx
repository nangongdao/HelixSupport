/**
 * Helix Support — admin island (D3 long tail: admin domain).
 *
 * Owns the whole #adminContent card grid in the desktop shell: tenant
 * quota, members, webhooks, report subscriptions/export, CSAT summary,
 * SLA policies and automatic routing rules. Mounts into #adminReactIsland;
 * the eight legacy .admin-card sections are yielded (hidden) while the
 * mount exists.
 *
 * Reads go through react-query (one query per card domain, all disabled
 * until helix-identity reports admin:manage — a non-admin island must
 * never issue a privileged request, matching tests/ui_admin.py). Writes
 * bridge back to legacy via helix-admin-* events so api()/showToast()/
 * window.confirm() and the reload lifecycle stay in app.js, exactly like
 * the knowledge island's save bridge.
 *
 * The island keeps the legacy class contract (.admin-card/.admin-member/
 * .admin-webhook/…) so the desktop axe pass and any class-based desktop
 * checks keep resolving; element ids get a React suffix because the
 * yielded legacy tree keeps the originals.
 *
 * See DESKTOP_TAURI_PLAN.md §D3 (app.js long tail).
 */

import React, { useCallback, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  QueryClient,
  QueryClientProvider,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

/* ── domain constants (verbatim from app.js / js/admin-report.js) ───── */

const WEBHOOK_EVENTS = [
  ["conversation.created", "会话创建"],
  ["conversation.escalated", "升级人工"],
  ["conversation.resolved", "会话解决"],
  ["conversation.sla_breached", "SLA 违约"],
  ["conversation.sla_impending", "SLA 临近"],
  ["report.generated", "报表生成"],
];

const ROLE_LABELS = {
  admin: "管理员",
  supervisor: "主管",
  operator: "客服",
  channel: "渠道",
  viewer: "只读",
  auditor: "审计员",
};

const REPORT_TYPE_LABELS = { quality: "质量报表", usage: "使用量报表" };
const SCHEDULE_LABELS = { daily: "每日", weekly: "每周" };
const PRIORITY_LABELS = { normal: "普通", high: "高优" };

export const ADMIN_EVENTS = Object.freeze({
  IDENTITY: "helix-identity",
  REFRESH: "helix-admin-refresh",
  SAVED: "helix-admin-saved",
  SAVE_QUOTA: "helix-admin-save-quota",
  INVITE_MEMBER: "helix-admin-invite-member",
  MEMBER_ROLE: "helix-admin-member-role",
  MEMBER_DEACTIVATE: "helix-admin-member-deactivate",
  REGISTER_WEBHOOK: "helix-admin-register-webhook",
  DELETE_WEBHOOK: "helix-admin-delete-webhook",
  CREATE_SUBSCRIPTION: "helix-admin-create-subscription",
  TOGGLE_SUBSCRIPTION: "helix-admin-toggle-subscription",
  DELETE_SUBSCRIPTION: "helix-admin-delete-subscription",
  GENERATE_REPORT: "helix-admin-generate-report",
  REPORT_GENERATED: "helix-admin-report-generated",
  SAVE_SLA: "helix-admin-save-sla",
  CREATE_RULE: "helix-admin-create-rule",
  DELETE_RULE: "helix-admin-delete-rule",
});

/** React-suffixed ids: the yielded legacy cards keep the originals. */
export const CARD_IDS = Object.freeze({
  quotaReadout: "quotaReadoutReact",
  quotaForm: "quotaFormReact",
  quotaConversations: "quotaConversationsReact",
  quotaStorageMb: "quotaStorageMbReact",
  memberList: "memberListReact",
  memberForm: "memberFormReact",
  memberActorId: "memberActorIdReact",
  memberRole: "memberRoleReact",
  webhookList: "webhookListReact",
  webhookForm: "webhookFormReact",
  webhookUrl: "webhookUrlReact",
  webhookEvents: "webhookEventsReact",
  webhookSecret: "webhookSecretReact",
  reportSubscriptionList: "reportSubscriptionListReact",
  reportSubscriptionForm: "reportSubscriptionFormReact",
  reportType: "reportTypeReact",
  reportSchedule: "reportScheduleReact",
  reportWindowDays: "reportWindowDaysReact",
  reportWebhook: "reportWebhookReact",
  reportGenerateForm: "reportGenerateFormReact",
  reportGenerateType: "reportGenerateTypeReact",
  reportGenerateWindow: "reportGenerateWindowReact",
  reportPreview: "reportPreviewReact",
  csatReadout: "csatReadoutReact",
  csatTrend: "csatTrendReact",
  slaPolicyList: "slaPolicyListReact",
  slaPolicyForm: "slaPolicyFormReact",
  slaPriority: "slaPriorityReact",
  slaChannel: "slaChannelReact",
  slaFirstResponse: "slaFirstResponseReact",
  slaResolve: "slaResolveReact",
  routingRuleList: "routingRuleListReact",
  routingRuleForm: "routingRuleFormReact",
  ruleIntent: "ruleIntentReact",
  ruleLabel: "ruleLabelReact",
  ruleChannel: "ruleChannelReact",
  ruleGroup: "ruleGroupReact",
  rulePriority: "rulePriorityReact",
});

/* ── pure models (verbatim logic from app.js render* / admin-report.js,
 *    §43.6 framework-agnostic; ctx reads replaced by parameters) ────── */

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

/* ── identity (deterministic permission gate) ────────────────────────── */

function currentIdentity() {
  return {
    role: (typeof window !== "undefined" && window.__HELIX_ROLE__) || "guest",
    permissions:
      (typeof window !== "undefined" && window.__HELIX_PERMISSIONS__) || [],
    actorId: (typeof window !== "undefined" && window.__HELIX_ACTOR__) || "",
    tenantId:
      (typeof document !== "undefined" && document.documentElement.dataset.tenantId) ||
      (typeof window !== "undefined" && window.__HELIX_TENANT__) ||
      "demo",
  };
}

/**
 * Identity arrives from legacy's /api/me after this island mounts, so
 * globals alone are a race. app.js dispatches helix-identity (detail =
 * {role, permissions, actorId, tenantId}) whenever the authenticated
 * operator changes; until then canManage is false and every query stays
 * disabled — no privileged fetch can fire early.
 */
export function useIdentity() {
  const [identity, setIdentity] = useState(currentIdentity);
  useEffect(() => {
    const sync = (event) =>
      setIdentity(event.detail ? { ...event.detail } : currentIdentity());
    window.addEventListener(ADMIN_EVENTS.IDENTITY, sync);
    return () => window.removeEventListener(ADMIN_EVENTS.IDENTITY, sync);
  }, []);
  return identity;
}

/** admin:manage gates the whole page (app.js canManage parity). */
export function canManageIdentity(identity) {
  return (
    Array.isArray(identity.permissions) &&
    identity.permissions.includes("admin:manage")
  );
}

/* ── shared bits ─────────────────────────────────────────────────────── */

function useBridge() {
  return useCallback((type, detail) => {
    window.dispatchEvent(new CustomEvent(type, { detail }));
  }, []);
}

function AdminReadout({ id, rows }) {
  return (
    <dl id={id} className="admin-readout">
      {rows.map(([term, detail]) => (
        <React.Fragment key={term}>
          <dt>{term}</dt>
          <dd>{detail}</dd>
        </React.Fragment>
      ))}
    </dl>
  );
}

function AdminEmpty({ children }) {
  return <li className="admin-empty">{children}</li>;
}

/**
 * Legacy forms clear their inputs only after a successful write. The
 * bridge reports the outcome through helix-admin-saved; this hook clears
 * the given setters when a matching successful save lands.
 */
function useClearOnSaved(domains, clear) {
  useEffect(() => {
    const onSaved = (event) => {
      const { ok, domains: savedDomains } = event.detail || {};
      if (!ok) return;
      const wanted = Array.isArray(savedDomains) ? savedDomains : [];
      if (domains.some((domain) => wanted.includes(domain))) clear();
    };
    window.addEventListener(ADMIN_EVENTS.SAVED, onSaved);
    return () => window.removeEventListener(ADMIN_EVENTS.SAVED, onSaved);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}

/* ── quota card ──────────────────────────────────────────────────────── */

function AdminQuotaCard({ quota }) {
  const bridge = useBridge();
  const [conversations, setConversations] = useState("");
  const [storageMb, setStorageMb] = useState("");
  useClearOnSaved(["quota"], () => {
    setConversations("");
    setStorageMb("");
  });
  const submit = (event) => {
    event.preventDefault();
    const body = {};
    if (conversations !== "") body.conversation_quota = Number(conversations);
    if (storageMb !== "") body.storage_quota_bytes = Number(storageMb) * 1024 * 1024;
    if (!Object.keys(body).length) return;
    bridge(ADMIN_EVENTS.SAVE_QUOTA, body);
  };
  return (
    <section className="admin-card" aria-label="租户配额">
      <h3>租户配额</h3>
      <AdminReadout id={CARD_IDS.quotaReadout} rows={quotaReadoutRows(quota || {})} />
      <form id={CARD_IDS.quotaForm} className="admin-form" onSubmit={submit}>
        <label className="admin-field">会话配额
          <input
            id={CARD_IDS.quotaConversations}
            type="number"
            min="1"
            max="10000000"
            placeholder="不修改留空"
            value={conversations}
            onChange={(e) => setConversations(e.target.value)}
          />
        </label>
        <label className="admin-field">存储配额（MB）
          <input
            id={CARD_IDS.quotaStorageMb}
            type="number"
            min="1"
            placeholder="不修改留空"
            value={storageMb}
            onChange={(e) => setStorageMb(e.target.value)}
          />
        </label>
        <button className="button button-primary" type="submit">保存配额</button>
      </form>
    </section>
  );
}

/* ── members card ────────────────────────────────────────────────────── */

function AdminMembersCard({ members, selfActor }) {
  const bridge = useBridge();
  const [actorId, setActorId] = useState("");
  const [role, setRole] = useState("operator");
  useClearOnSaved(["members"], () => setActorId(""));
  const rows = (members || []).map((member) => memberRowModel(member, selfActor));
  const submit = (event) => {
    event.preventDefault();
    const trimmed = actorId.trim();
    if (!trimmed) return;
    bridge(ADMIN_EVENTS.INVITE_MEMBER, { actorId: trimmed, role });
  };
  return (
    <section className="admin-card" aria-label="成员">
      <h3>成员</h3>
      <ul id={CARD_IDS.memberList} className="admin-list">
        {!rows.length && <AdminEmpty>暂无成员</AdminEmpty>}
        {rows.map((row) => (
          <li className="admin-member" key={row.actorId}>
            <span className="admin-member-actor">
              {row.actorId}
              {row.isSelf ? "（你）" : ""}
            </span>
            <span className="admin-member-role">{row.roleLabel}</span>
            <span className="admin-member-actions">
              <select
                className="admin-ghost-button member-role-select"
                data-actor={row.actorId}
                aria-label="变更角色"
                disabled={row.isSelf}
                value={row.role}
                onChange={(e) =>
                  bridge(ADMIN_EVENTS.MEMBER_ROLE, { actorId: row.actorId, role: e.target.value })
                }
              >
                {Object.entries(ROLE_LABELS).map(([value, label]) => (
                  <option value={value} key={value}>{label}</option>
                ))}
              </select>
              {row.deactivated ? (
                <span className="admin-member-role">已停用</span>
              ) : (
                <button
                  type="button"
                  className="admin-ghost-button member-deactivate"
                  data-actor={row.actorId}
                  disabled={row.isSelf}
                  onClick={() => bridge(ADMIN_EVENTS.MEMBER_DEACTIVATE, { actorId: row.actorId })}
                >
                  停用
                </button>
              )}
            </span>
          </li>
        ))}
      </ul>
      <form id={CARD_IDS.memberForm} className="admin-form" onSubmit={submit}>
        <label className="admin-field">坐席标识
          <input
            id={CARD_IDS.memberActorId}
            type="text"
            maxLength={80}
            pattern="[A-Za-z0-9._:@\-]{2,}"
            title="2–80 位，仅字母、数字、. _ : @ -"
            placeholder="例如 operator-2"
            required
            value={actorId}
            onChange={(e) => setActorId(e.target.value)}
          />
        </label>
        <label className="admin-field">角色
          <select
            id={CARD_IDS.memberRole}
            value={role}
            onChange={(e) => setRole(e.target.value)}
          >
            <option value="operator">客服</option>
            <option value="supervisor">主管</option>
            <option value="admin">管理员</option>
            <option value="viewer">只读</option>
            <option value="auditor">审计员</option>
            <option value="channel">渠道</option>
          </select>
        </label>
        <button className="button button-primary" type="submit">邀请成员</button>
      </form>
    </section>
  );
}

/* ── webhooks card ───────────────────────────────────────────────────── */

function AdminWebhooksCard({ webhooks }) {
  const bridge = useBridge();
  const [url, setUrl] = useState("");
  const [secret, setSecret] = useState("");
  const [events, setEvents] = useState([]);
  useClearOnSaved(["webhooks"], () => {
    setUrl("");
    setSecret("");
    setEvents([]);
  });
  const toggleEvent = (event, checked) => {
    setEvents((prev) =>
      checked ? [...prev, event] : prev.filter((item) => item !== event),
    );
  };
  const submit = (event) => {
    event.preventDefault();
    const trimmedUrl = url.trim();
    const trimmedSecret = secret.trim();
    if (!trimmedUrl || !trimmedSecret || !events.length) return;
    bridge(ADMIN_EVENTS.REGISTER_WEBHOOK, {
      url: trimmedUrl,
      secret: trimmedSecret,
      events: [...events],
    });
  };
  return (
    <section className="admin-card" aria-label="Webhook">
      <h3>Webhook</h3>
      <ul id={CARD_IDS.webhookList} className="admin-list">
        {!(webhooks || []).length && <AdminEmpty>暂无 Webhook</AdminEmpty>}
        {(webhooks || []).map((hook) => (
          <li className="admin-webhook" key={hook.id}>
            <span className="admin-webhook-url" title={hook.url}>{hook.url}</span>
            <span className="admin-webhook-events">{hook.events.join(" · ")}</span>
            <button
              type="button"
              className="admin-ghost-button webhook-delete"
              data-id={hook.id}
              onClick={() => bridge(ADMIN_EVENTS.DELETE_WEBHOOK, { id: hook.id })}
            >
              删除
            </button>
          </li>
        ))}
      </ul>
      <form id={CARD_IDS.webhookForm} className="admin-form" onSubmit={submit}>
        <label className="admin-field">URL
          <input
            id={CARD_IDS.webhookUrl}
            type="url"
            maxLength={500}
            autoComplete="url"
            placeholder="https://example.com/hook"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
          />
        </label>
        <fieldset className="admin-events">
          <legend>事件</legend>
          <div id={CARD_IDS.webhookEvents} className="admin-events-grid">
            {WEBHOOK_EVENTS.map(([event, label]) => (
              <label key={event}>
                <input
                  type="checkbox"
                  value={event}
                  checked={events.includes(event)}
                  onChange={(e) => toggleEvent(event, e.target.checked)}
                />
                {label}
              </label>
            ))}
          </div>
        </fieldset>
        <label className="admin-field">签名密钥
          <input
            id={CARD_IDS.webhookSecret}
            type="password"
            minLength={8}
            maxLength={200}
            autoComplete="new-password"
            spellCheck={false}
            placeholder="至少 8 位"
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
          />
        </label>
        <button className="button button-primary" type="submit">注册 Webhook</button>
      </form>
    </section>
  );
}

/* ── report subscriptions card ───────────────────────────────────────── */

function AdminSubscriptionsCard({ subscriptions, webhooks }) {
  const bridge = useBridge();
  const options = activeWebhookOptions(webhooks);
  const [reportType, setReportType] = useState("quality");
  const [schedule, setSchedule] = useState("daily");
  const [windowDays, setWindowDays] = useState("7");
  const [webhookId, setWebhookId] = useState("");
  const submit = (event) => {
    event.preventDefault();
    const endpointId = webhookId || (options[0] ? options[0].id : "");
    if (!endpointId) return; // 桥会以 legacy 同款 toast 提示
    bridge(ADMIN_EVENTS.CREATE_SUBSCRIPTION, {
      reportType,
      schedule,
      windowDays: Number(windowDays || 7),
      webhookEndpointId: endpointId,
    });
  };
  return (
    <section className="admin-card" aria-label="报表订阅">
      <h3>报表订阅</h3>
      <ul id={CARD_IDS.reportSubscriptionList} className="admin-list">
        {!(subscriptions || []).length && <AdminEmpty>暂无报表订阅</AdminEmpty>}
        {(subscriptions || []).map((sub) => {
          const row = subscriptionRowModel(sub);
          return (
            <li className="admin-report-sub" data-id={row.id} key={row.id}>
              <span className="admin-report-sub-main">
                <span className="admin-report-sub-title">{row.title}</span>
                <span className="admin-report-sub-meta">{row.meta}</span>
              </span>
              <span className="admin-member-actions">
                <span className={`status-pill${row.active ? "" : " is-ticket-closed"}`}>
                  {row.active ? "启用" : "停用"}
                </span>
                <button
                  type="button"
                  className="admin-ghost-button report-sub-toggle"
                  data-id={row.id}
                  onClick={() => bridge(ADMIN_EVENTS.TOGGLE_SUBSCRIPTION, { id: row.id, active: !row.active })}
                >
                  {row.active ? "停用" : "启用"}
                </button>
                <button
                  type="button"
                  className="admin-ghost-button report-sub-delete"
                  data-id={row.id}
                  onClick={() => bridge(ADMIN_EVENTS.DELETE_SUBSCRIPTION, { id: row.id })}
                >
                  删除
                </button>
              </span>
            </li>
          );
        })}
      </ul>
      <form id={CARD_IDS.reportSubscriptionForm} className="admin-form" onSubmit={submit}>
        <label className="admin-field">报表
          <select id={CARD_IDS.reportType} value={reportType} onChange={(e) => setReportType(e.target.value)}>
            <option value="quality">质量报表</option>
            <option value="usage">使用量报表</option>
          </select>
        </label>
        <label className="admin-field">周期
          <select id={CARD_IDS.reportSchedule} value={schedule} onChange={(e) => setSchedule(e.target.value)}>
            <option value="daily">每日</option>
            <option value="weekly">每周</option>
          </select>
        </label>
        <label className="admin-field">窗口（天）
          <input
            id={CARD_IDS.reportWindowDays}
            type="number"
            min="1"
            max="30"
            value={windowDays}
            onChange={(e) => setWindowDays(e.target.value)}
          />
        </label>
        <label className="admin-field">Webhook 端点
          <select
            id={CARD_IDS.reportWebhook}
            value={webhookId}
            onChange={(e) => setWebhookId(e.target.value)}
          >
            {options.length
              ? options.map((hook) => (
                  <option value={hook.id} key={hook.id}>{hook.url}</option>
                ))
              : <option value="">暂无可用 Webhook（请先注册）</option>}
          </select>
        </label>
        <button className="button button-primary" type="submit">创建订阅</button>
      </form>
    </section>
  );
}

/* ── report export card ──────────────────────────────────────────────── */

function AdminReportExportCard() {
  const bridge = useBridge();
  const [reportType, setReportType] = useState("quality");
  const [windowDays, setWindowDays] = useState("7");
  const [preview, setPreview] = useState(null);
  const generate = (event) => {
    event.preventDefault();
    setPreview(null);
    bridge(ADMIN_EVENTS.GENERATE_REPORT, {
      reportType,
      windowDays: Number(windowDays || 7),
    });
  };
  // 下载由带鉴权 cookie 的导航触发(attachment disposition)——不需要桥。
  const exportCsv = (event) => {
    event.preventDefault();
    window.location.href = reportExportUrl(reportType, Number(windowDays || 7));
  };
  useEffect(() => {
    const onGenerated = (event) => {
      const { ok, text } = event.detail || {};
      if (ok) setPreview(text);
    };
    window.addEventListener(ADMIN_EVENTS.REPORT_GENERATED, onGenerated);
    return () => window.removeEventListener(ADMIN_EVENTS.REPORT_GENERATED, onGenerated);
  }, []);
  // React's synthetic event does not proxy `submitter` — legacy reads it
  // off the native event (js/admin-report.js bindAdminReports), so the
  // island must too, or the export branch is unreachable in real browsers.
  const submit = (event) => {
    if (event.nativeEvent.submitter?.value === "export") exportCsv(event);
    else generate(event);
  };
  return (
    <section className="admin-card" aria-label="报表导出">
      <h3>报表导出</h3>
      <form id={CARD_IDS.reportGenerateForm} className="admin-form" onSubmit={submit}>
        <label className="admin-field">报表
          <select
            id={CARD_IDS.reportGenerateType}
            value={reportType}
            onChange={(e) => setReportType(e.target.value)}
          >
            <option value="quality">质量报表</option>
            <option value="usage">使用量报表</option>
          </select>
        </label>
        <label className="admin-field">窗口（天）
          <input
            id={CARD_IDS.reportGenerateWindow}
            type="number"
            min="1"
            max="30"
            value={windowDays}
            onChange={(e) => setWindowDays(e.target.value)}
          />
        </label>
        <div className="admin-form-row">
          <button className="button button-secondary" type="submit" name="mode" value="generate">生成预览</button>
          <button className="button button-secondary" type="submit" name="mode" value="export">导出 CSV</button>
        </div>
        <pre id={CARD_IDS.reportPreview} className="report-preview" hidden={preview == null}>
          {preview ?? ""}
        </pre>
      </form>
    </section>
  );
}

/* ── CSAT card ───────────────────────────────────────────────────────── */

function AdminCsatCard({ csat }) {
  const model = csatModel(csat || {});
  return (
    <section className="admin-card" aria-label="CSAT 评分汇总">
      <h3>CSAT 评分汇总</h3>
      <AdminReadout id={CARD_IDS.csatReadout} rows={model.rows} />
      <ul id={CARD_IDS.csatTrend} className="admin-list">
        {!model.trend.length && <AdminEmpty>暂无已回收的评分</AdminEmpty>}
        {model.trend.map((day) => (
          <li className="csat-day" key={day.date}>
            <span className="csat-day-date">{day.date}</span>
            <span className="csat-day-meta">{day.meta}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

/* ── SLA policies card ───────────────────────────────────────────────── */

function AdminSlaCard({ policies }) {
  const bridge = useBridge();
  const [priority, setPriority] = useState("");
  const [channel, setChannel] = useState("");
  const [firstResponse, setFirstResponse] = useState("15");
  const [resolve, setResolve] = useState("1440");
  const fillFrom = (policy) => {
    setPriority(policy.priority || "");
    setChannel(policy.channel || "");
    setFirstResponse(String(policy.first_response_minutes));
    setResolve(String(policy.resolve_minutes));
  };
  const submit = (event) => {
    event.preventDefault();
    bridge(ADMIN_EVENTS.SAVE_SLA, {
      priority: priority || null,
      channel: channel.trim() || null,
      firstResponseMinutes: Number(firstResponse || 0),
      resolveMinutes: Number(resolve || 0),
    });
  };
  return (
    <section className="admin-card" aria-label="SLA 策略">
      <h3>SLA 策略</h3>
      <ul id={CARD_IDS.slaPolicyList} className="admin-list">
        {!(policies || []).length && <AdminEmpty>暂无 SLA 策略（走全局默认）</AdminEmpty>}
        {(policies || []).map((policy) => (
          <li className="sla-rule-row" data-id={policy.id} key={policy.id}>
            <span className="sla-rule-main">
              <span className="sla-rule-title">{slaPolicyLabel(policy)}</span>
              <span className="sla-rule-meta">
                首响 {policy.first_response_minutes}min · 解决 {policy.resolve_minutes}min
              </span>
            </span>
            <span className="admin-member-actions">
              <button
                type="button"
                className="admin-ghost-button sla-policy-fill"
                data-id={policy.id}
                title="填入表单编辑"
                onClick={() => fillFrom(policy)}
              >
                编辑
              </button>
            </span>
          </li>
        ))}
      </ul>
      <form id={CARD_IDS.slaPolicyForm} className="admin-form" onSubmit={submit}>
        <label className="admin-field">适用级别
          <select id={CARD_IDS.slaPriority} value={priority} onChange={(e) => setPriority(e.target.value)}>
            <option value="">默认（全局）</option>
            <option value="normal">普通</option>
            <option value="high">高优</option>
          </select>
        </label>
        <label className="admin-field">渠道
          <input
            id={CARD_IDS.slaChannel}
            type="text"
            maxLength={40}
            placeholder="留空表示全部"
            value={channel}
            onChange={(e) => setChannel(e.target.value)}
          />
        </label>
        <label className="admin-field">首响时限（分钟）
          <input
            id={CARD_IDS.slaFirstResponse}
            type="number"
            min="1"
            max="10080"
            value={firstResponse}
            onChange={(e) => setFirstResponse(e.target.value)}
          />
        </label>
        <label className="admin-field">解决时限（分钟）
          <input
            id={CARD_IDS.slaResolve}
            type="number"
            min="1"
            max="10080"
            value={resolve}
            onChange={(e) => setResolve(e.target.value)}
          />
        </label>
        <button className="button button-primary" type="submit">保存策略</button>
      </form>
    </section>
  );
}

/* ── routing rules card ──────────────────────────────────────────────── */

function AdminRoutingCard({ rules, groups }) {
  const bridge = useBridge();
  const [intent, setIntent] = useState("");
  const [label, setLabel] = useState("");
  const [channel, setChannel] = useState("");
  const [groupId, setGroupId] = useState("");
  const [priority, setPriority] = useState("0");
  useClearOnSaved(["rules"], () => {
    setIntent("");
    setLabel("");
    setChannel("");
  });
  const submit = (event) => {
    event.preventDefault();
    const effectiveGroup = groupId || (groups && groups[0] ? groups[0].id : "");
    if (!effectiveGroup) return; // 桥会以 legacy 同款 toast 提示
    bridge(ADMIN_EVENTS.CREATE_RULE, {
      intent: intent.trim(),
      label: label.trim(),
      channel: channel.trim(),
      groupId: effectiveGroup,
      priority: Number(priority || 0),
    });
  };
  return (
    <section className="admin-card" aria-label="自动路由规则">
      <h3>自动路由规则</h3>
      <ul id={CARD_IDS.routingRuleList} className="admin-list">
        {!(rules || []).length && <AdminEmpty>暂无路由规则</AdminEmpty>}
        {(rules || []).map((rule) => (
          <li className="routing-rule-row" data-id={rule.id} key={rule.id}>
            <span className="routing-rule-main">
              <span className="routing-rule-title">{routingRuleLabel(rule)}</span>
              <span className="routing-rule-meta">{routingRuleMeta(rule, groups)}</span>
            </span>
            <span className="admin-member-actions">
              <button
                type="button"
                className="admin-ghost-button routing-rule-delete"
                data-id={rule.id}
                onClick={() => bridge(ADMIN_EVENTS.DELETE_RULE, { id: rule.id })}
              >
                删除
              </button>
            </span>
          </li>
        ))}
      </ul>
      <form id={CARD_IDS.routingRuleForm} className="admin-form" onSubmit={submit}>
        <label className="admin-field">意图
          <input
            id={CARD_IDS.ruleIntent}
            type="text"
            maxLength={80}
            placeholder="可选"
            value={intent}
            onChange={(e) => setIntent(e.target.value)}
          />
        </label>
        <label className="admin-field">标签
          <input
            id={CARD_IDS.ruleLabel}
            type="text"
            maxLength={80}
            placeholder="可选"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
          />
        </label>
        <label className="admin-field">渠道
          <input
            id={CARD_IDS.ruleChannel}
            type="text"
            maxLength={40}
            placeholder="可选"
            value={channel}
            onChange={(e) => setChannel(e.target.value)}
          />
        </label>
        <label className="admin-field">分配组
          <select id={CARD_IDS.ruleGroup} value={groupId} onChange={(e) => setGroupId(e.target.value)}>
            {(groups || []).length
              ? groups.map((group) => (
                  <option value={group.id} key={group.id}>{group.name}</option>
                ))
              : <option value="">暂无坐席组（请先创建）</option>}
          </select>
        </label>
        <label className="admin-field">优先级
          <input
            id={CARD_IDS.rulePriority}
            type="number"
            min="0"
            max="1000"
            value={priority}
            onChange={(e) => setPriority(e.target.value)}
          />
        </label>
        <button className="button button-primary" type="submit">添加规则</button>
      </form>
    </section>
  );
}

/* ── island root ─────────────────────────────────────────────────────── */

// Namespace-style keys so refetchQueries({ queryKey: ["admin"] }) covers
// every admin query through react-query's element-wise prefix matching.
const QUERY_DEFS = [
  { key: ["admin", "quota"], domain: "quota", path: (tenantId) => `/api/admin/tenants/${encodeURIComponent(tenantId)}/quota` },
  { key: ["admin", "members"], domain: "members", path: (tenantId) => `/api/admin/tenants/${encodeURIComponent(tenantId)}/members` },
  { key: ["admin", "webhooks"], domain: "webhooks", path: () => "/api/webhooks" },
  { key: ["admin", "subscriptions"], domain: "subscriptions", path: () => "/api/admin/report-subscriptions" },
  { key: ["admin", "sla"], domain: "sla", path: () => "/api/admin/sla-policies" },
  { key: ["admin", "groups"], domain: "groups", path: () => "/api/admin/agent-groups" },
  { key: ["admin", "rules"], domain: "rules", path: () => "/api/admin/routing-rules" },
  { key: ["admin", "csat"], domain: "csat", path: () => "/api/admin/csat-summary" },
];

/** Legacy api() contract: tenant header from the host document. */
async function fetchAdminJson(path) {
  const tenant = document.documentElement.dataset.tenantId || "demo";
  const res = await fetch(path, { headers: { "X-Tenant-Id": tenant } });
  if (!res.ok) throw new Error(`admin API ${res.status} ${path}`);
  return res.json();
}

/** helix-admin-saved domains → the query keys a refetch must cover. */
export function domainsToQueryKeys(domains) {
  const list = Array.isArray(domains) ? domains : [];
  const wanted = list.length ? list : QUERY_DEFS.map((def) => def.domain);
  return QUERY_DEFS.filter((def) => wanted.includes(def.domain)).map((def) => def.key);
}

export function AdminIsland() {
  const identity = useIdentity();
  const allowed = canManageIdentity(identity);
  const queryClient = useQueryClient();
  const tenantId = identity.tenantId || document.documentElement.dataset.tenantId || "demo";

  const queries = QUERY_DEFS.map((def) =>
    useQuery({
      queryKey: def.key,
      queryFn: () => fetchAdminJson(def.path(tenantId)),
      //Legacy loadAdminView 在每次进入视图/写操作后全量重拉——岛上的
      // 数据新鲜度由 helix-admin-refresh / helix-admin-saved 显式驱动;
      // enabled 由身份事件驱动,非管理员岛绝不发特权请求。
      enabled: allowed,
      staleTime: 15_000,
    }),
  );

  // Legacy owns the view header buttons (刷新) and every write; both drive
  // the island through these two events.
  useEffect(() => {
    const onRefresh = (event) => {
      if (!allowed) return;
      if (event.detail?.force) {
        void queryClient.refetchQueries({ queryKey: ["admin"] });
        return;
      }
      void queryClient.refetchQueries({ queryKey: ["admin"], stale: true });
    };
    const onSaved = (event) => {
      const { ok, domains } = event.detail || {};
      if (ok === false) return; // 桥已 toast,岛保持当前数据
      for (const key of domainsToQueryKeys(domains)) {
        void queryClient.invalidateQueries({ queryKey: key });
      }
    };
    window.addEventListener(ADMIN_EVENTS.REFRESH, onRefresh);
    window.addEventListener(ADMIN_EVENTS.SAVED, onSaved);
    return () => {
      window.removeEventListener(ADMIN_EVENTS.REFRESH, onRefresh);
      window.removeEventListener(ADMIN_EVENTS.SAVED, onSaved);
    };
  }, [allowed, queryClient]);

  // 非管理员渲染 null:拒绝面板由 legacy #adminDenied 负责(在让位卡片
  // 之外),而 enabled:false 保证没有任何特权请求会发出。
  if (!allowed) return null;

  const data = Object.fromEntries(
    QUERY_DEFS.map((def, index) => [def.domain, queries[index].data ?? null]),
  );

  return (
    <div className="admin-cards">
      <AdminQuotaCard quota={data.quota} />
      <AdminMembersCard members={data.members} selfActor={identity.actorId} />
      <AdminWebhooksCard webhooks={data.webhooks} />
      <AdminSubscriptionsCard subscriptions={data.subscriptions} webhooks={data.webhooks} />
      <AdminReportExportCard />
      <AdminCsatCard csat={data.csat} />
      <AdminSlaCard policies={data.sla} />
      <AdminRoutingCard rules={data.rules} groups={data.groups} />
    </div>
  );
}

/**
 * Mount the admin island into a host <div>. Called by the island loader.
 * @param {HTMLElement} element - mount point
 */
export function mount(element) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { refetchOnWindowFocus: false } },
  });
  const root = createRoot(element);
  root.render(
    <QueryClientProvider client={queryClient}>
      <AdminIsland />
    </QueryClientProvider>,
  );
}

export default { mount, AdminIsland };
