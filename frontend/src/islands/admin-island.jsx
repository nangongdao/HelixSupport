/**
 * Helix Support — admin island (D3 long tail: admin domain).
 *
 * Owns the whole #adminContent card grid in the desktop shell: tenant
 * quota, members, webhooks, report subscriptions/export, CSAT summary,
 * cost dashboard, SLA policies and automatic routing rules. Mounts into
 * #adminReactIsland; the eight legacy .admin-card sections are yielded
 * (hidden) while the mount exists. The cost dashboard card is
 * island-native (2.3.0 shipped the analytics API without a legacy
 * section), so the grid renders nine cards.
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
 * This file is the composition root: the constants, pure models, identity
 * gate and eight cards live under ./admin/ (the domain outgrew the
 * project's 400-line module limit). Every previously exported name is
 * re-exported here so importers — the component test and the vite entry —
 * are unchanged.
 *
 * See DESKTOP_TAURI_PLAN.md §D3 (app.js long tail).
 */

import React, { useEffect } from "react";
import { createRoot } from "react-dom/client";
import {
  QueryClient,
  QueryClientProvider,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import { ADMIN_EVENTS, CARD_IDS } from "./admin/constants.js";
import { canManageIdentity, useIdentity } from "./admin/shared.jsx";
import {
  AdminQuotaCard,
  AdminMembersCard,
  AdminWebhooksCard,
} from "./admin/tenant-cards.jsx";
import {
  AdminCsatCard,
  AdminReportExportCard,
  AdminSubscriptionsCard,
} from "./admin/report-cards.jsx";
import { AdminCostCard } from "./admin/cost-card.jsx";
import { AdminGovernanceCard } from "./admin/governance-card.jsx";
import { AdminRoutingCard, AdminSlaCard } from "./admin/policy-cards.jsx";

export { ADMIN_EVENTS, CARD_IDS } from "./admin/constants.js";
export { canManageIdentity, useIdentity } from "./admin/shared.jsx";
export {
  activeWebhookOptions,
  costAnomalyModel,
  costBreakdownItems,
  costSummaryRows,
  governanceApprovalItems,
  governanceDatasetRows,
  governanceEvalRunItems,
  governanceFeedbackItems,
  csatModel,
  formatTime,
  formatUsd,
  memberRowModel,
  quotaReadoutRows,
  reportExportUrl,
  routingRuleLabel,
  routingRuleMeta,
  slaPolicyLabel,
  subscriptionRowModel,
} from "./admin/models.js";

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
  { key: ["admin", "cost-daily"], domain: "cost-daily", path: () => "/api/analytics/costs/daily" },
  { key: ["admin", "cost-agents"], domain: "cost-agents", path: () => "/api/analytics/costs/by_agent" },
  { key: ["admin", "cost-prompts"], domain: "cost-prompts", path: () => "/api/analytics/costs/by_prompt" },
  { key: ["admin", "cost-anomaly"], domain: "cost-anomaly", path: () => "/api/analytics/costs/anomaly" },
  {
    key: ["admin", "governance-approvals"],
    domain: "governance-approvals",
    path: () => "/api/admin/governance/approvals?status=pending",
  },
  {
    key: ["admin", "governance-feedback"],
    domain: "governance-feedback",
    path: () => "/api/admin/governance/feedback?status=pending_review",
  },
  { key: ["admin", "governance-datasets"], domain: "governance-datasets", path: () => "/api/admin/governance/datasets" },
  {
    key: ["admin", "governance-eval-runs"],
    domain: "governance-eval-runs",
    path: () => "/api/admin/governance/eval-runs",
  },
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
      <AdminCostCard
        daily={data["cost-daily"]}
        agents={data["cost-agents"]}
        prompts={data["cost-prompts"]}
        anomaly={data["cost-anomaly"]}
      />
      <AdminGovernanceCard
        approvals={data["governance-approvals"]}
        feedback={data["governance-feedback"]}
        datasets={data["governance-datasets"]}
        evalRuns={data["governance-eval-runs"]}
      />
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
