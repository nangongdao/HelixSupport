/**
 * Helix Support — admin island component tests (D3 long tail)
 *
 * The island owns the whole #adminContent card grid, but every write
 * bridges back to legacy via helix-admin-* events and identity arrives via
 * helix-identity. These tests cover the identity gate (no privileged fetch
 * before admin:manage — the invariant tests/ui_admin.py asserts for the
 * legacy denial), the pure card models, and the bridge contracts — no
 * legacy app.js, only a stubbed fetch router.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";

import {
  AdminIsland,
  ADMIN_EVENTS,
  CARD_IDS,
  canManageIdentity,
  domainsToQueryKeys,
  quotaReadoutRows,
  memberRowModel,
  subscriptionRowModel,
  slaPolicyLabel,
  routingRuleLabel,
  csatModel,
  costAnomalyModel,
  costBreakdownItems,
  costSummaryRows,
  formatUsd,
  reportExportUrl,
} from "./admin-island.jsx";

function makeQuota(overrides = {}) {
  return {
    tenant_id: "demo",
    name: "演示租户",
    conversation_quota: 1000,
    storage_quota_bytes: 64 * 1024 * 1024,
    daily_turn_budget: 5000,
    allowed_models: ["demo-model"],
    ...overrides,
  };
}

function makeMember(overrides = {}) {
  return {
    actor_id: "operator-2",
    role: "operator",
    status: "active",
    ...overrides,
  };
}

function makeWebhook(overrides = {}) {
  return {
    id: "hook_1",
    url: "https://example.com/hook",
    events: ["conversation.created"],
    status: "active",
    ...overrides,
  };
}

function makeSubscription(overrides = {}) {
  return {
    id: "sub_1",
    report_type: "quality",
    schedule: "daily",
    window_days: 7,
    webhook_endpoint_id: "hook_1",
    active: true,
    last_run_at: null,
    ...overrides,
  };
}

function makeSla(overrides = {}) {
  return {
    id: "sla_1",
    priority: "high",
    channel: "web",
    first_response_minutes: 10,
    resolve_minutes: 480,
    ...overrides,
  };
}

function makeRule(overrides = {}) {
  return {
    id: "rule_1",
    intent: "退款",
    group_id: "group_1",
    priority: 5,
    ...overrides,
  };
}

/** Route-map fetch stub: URL → JSON payload (mirrors legacy api() paths). */
function stubBackend(routes) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path) => {
      const route = Object.entries(routes).find(([match]) => path.startsWith(match));
      if (!route) throw new Error(`unexpected admin fetch: ${path}`);
      console.log("STUB-FETCH", path);
      return { ok: true, status: 200, json: async () => route[1] };
    }),
  );
}

const FULL_ROUTES = {
  "/api/admin/tenants/demo/quota": makeQuota(),
  "/api/admin/tenants/demo/members": [
    makeMember(),
    makeMember({ actor_id: "demo.admin", role: "admin" }),
  ],
  "/api/webhooks": [makeWebhook()],
  "/api/admin/report-subscriptions": [makeSubscription()],
  "/api/admin/sla-policies": [makeSla()],
  "/api/admin/agent-groups": [{ id: "group_1", name: "售后组" }],
  "/api/admin/routing-rules": [makeRule()],
  "/api/admin/csat-summary": { total: 4, avg_rating: 4.5, positive_rate: 0.75, per_day: [{ date: "2026-08-29", count: 4, avg_rating: 4.5 }] },
  "/api/analytics/costs/daily": {
    tenant_id: "demo",
    turn_count: 1234,
    prompt_tokens: 500000,
    completion_tokens: 125000,
    cost_usd: 4.321,
  },
  "/api/analytics/costs/by_agent": [
    { agent: "triage", turn_count: 700, prompt_tokens: 300000, completion_tokens: 1000, cost_usd: 0.9 },
    { agent: "summary", turn_count: 60, prompt_tokens: 120000, completion_tokens: 40000, cost_usd: 0.00045 },
  ],
  "/api/analytics/costs/by_prompt": [
    { prompt_version: "support-zh v7", turn_count: 900, prompt_tokens: 400000, completion_tokens: 100000, cost_usd: 3.5 },
  ],
  "/api/analytics/costs/anomaly": {
    anomaly: true,
    today_cost_usd: 2.5,
    baseline_cost_usd: 1.0,
    factor: 2.5,
  },
  "/api/admin/governance/approvals": [
    {
      id: "apr_gov1",
      subject_kind: "tool_enablement",
      subject_id: "knowledge.publish_bulk",
      requested_by: "supervisor-1",
      reason: "supervisor bulk publishing",
      decision: "pending",
    },
  ],
  "/api/admin/governance/feedback": [
    {
      id: "fbk_gov1",
      conversation_id: "conv_gov1",
      review_status: "pending_review",
      redacted_json: JSON.stringify({ rating: -1, reason: "答非所问", message_id: "msg_1" }),
    },
  ],
  "/api/admin/governance/eval-runs": [
    {
      id: "run_gov1",
      dataset_id: "ds_gov1",
      dataset_name: "browser-feedback",
      candidate: "triage-v9",
      baseline: "triage-v8",
      report_object_id: "eval-gov1",
      passed: 1,
      metrics_json: JSON.stringify({ accuracy: 0.95 }),
      created_at: "2026-09-06T00:00:00+00:00",
    },
  ],
  "/api/admin/governance/datasets": [
    { id: "ds_gov1", name: "browser-feedback", version: 1, strategy: "feedback", item_count: 4 },
  ],
};

function grantAdmin({ actorId = "demo.admin", tenantId = "demo" } = {}) {
  act(() => {
    window.dispatchEvent(
      new CustomEvent(ADMIN_EVENTS.IDENTITY, {
        detail: {
          role: "admin",
          permissions: ["admin:manage"],
          actorId,
          tenantId,
        },
      }),
    );
  });
}

async function renderIsland(routes = FULL_ROUTES, identity = "admin") {
  stubBackend(routes);
  document.documentElement.dataset.tenantId = "demo";
  if (identity === "admin") {
    window.__HELIX_ROLE__ = "admin";
    window.__HELIX_PERMISSIONS__ = ["admin:manage"];
    window.__HELIX_ACTOR__ = "demo.admin";
  } else {
    window.__HELIX_ROLE__ = identity;
    window.__HELIX_PERMISSIONS__ = ["conversation:read"];
    window.__HELIX_ACTOR__ = "browser.operator";
  }
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <AdminIsland />
    </QueryClientProvider>,
  );
  if (identity === "admin") {
    // 卡片在查询返回前就以空模型渲染;等数据真正落到 readout 再返回,
    // 让行级断言面对的是已加载的列表。
    await waitFor(() =>
      expect(
        document.getElementById(CARD_IDS.quotaReadout).textContent,
      ).toContain("演示租户"),
    );
  }
  return { client };
}

/** Identity lands only after mount — the exact race the gate must close. */
async function renderIslandWithoutIdentity(routes = FULL_ROUTES) {
  stubBackend(routes);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <AdminIsland />
    </QueryClientProvider>,
  );
  return { client };
}

function bridgeSpy() {
  return vi.spyOn(window, "dispatchEvent");
}

function eventsOfType(spy, type) {
  return spy.mock.calls.map(([ev]) => ev).filter((ev) => ev.type === type);
}

beforeEach(() => {
  delete window.__HELIX_ROLE__;
  delete window.__HELIX_PERMISSIONS__;
  delete window.__HELIX_ACTOR__;
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  delete document.documentElement.dataset.tenantId;
});

describe("identity gate", () => {
  it("renders nothing and fetches nothing without admin:manage", async () => {
    await renderIsland(FULL_ROUTES, "operator");
    expect(document.querySelector(".admin-cards")).toBeNull();
    expect(fetch).not.toHaveBeenCalled();
    // 非管理员也不应因后续事件解锁。
    act(() => {
      window.dispatchEvent(
        new CustomEvent(ADMIN_EVENTS.IDENTITY, {
          detail: { role: "operator", permissions: ["conversation:read"], actorId: "x", tenantId: "demo" },
        }),
      );
    });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(fetch).not.toHaveBeenCalled();
  });

  it("starts disabled before identity lands, then enables on helix-identity", async () => {
    const { client } = await renderIslandWithoutIdentity();
    expect(fetch).not.toHaveBeenCalled();
    grantAdmin();
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    await waitFor(() => expect(document.getElementById(CARD_IDS.quotaForm)).toBeTruthy());
    // quota + members hit the tenant-scoped paths with the legacy header.
    expect(fetch).toHaveBeenCalledWith(
      "/api/admin/tenants/demo/quota",
      expect.objectContaining({ headers: { "X-Tenant-Id": "demo" } }),
    );
  });
});

describe("pure card models", () => {
  it("quotaReadoutRows keeps the legacy readout contract", () => {
    const rows = quotaReadoutRows(makeQuota());
    expect(rows).toEqual([
      ["租户", "演示租户"],
      ["会话配额", 1000],
      ["存储配额", "64 MB"],
      ["每日 turn 预算", 5000],
      ["允许模型", "demo-model"],
    ]);
    const blank = quotaReadoutRows(makeQuota({ storage_quota_bytes: null, allowed_models: [] }));
    // legacy renderQuota 对空存储也拼 " MB" 后缀("— MB")——逐字保真。
    expect(blank[2][1]).toBe("— MB");
    expect(blank[4][1]).toBe("全部");
  });

  it("memberRowModel marks the self row for the Phase 32.1 guard", () => {
    const self = memberRowModel(makeMember({ actor_id: "me" }), "me");
    expect(self.isSelf).toBe(true);
    const deactivated = memberRowModel(makeMember({ status: "deactivated" }), "other");
    expect(deactivated.deactivated).toBe(true);
  });

  it("labels reuse the admin-report copy", () => {
    expect(slaPolicyLabel(makeSla())).toBe("高优 · 渠道 web");
    expect(slaPolicyLabel(makeSla({ priority: null, channel: null }))).toBe("默认 · 全渠道");
    expect(routingRuleLabel(makeRule())).toBe("意图 退款");
    expect(routingRuleLabel(makeRule({ intent: null }))).toBe("全部会话");
  });

  it("csatModel keeps the W2 empty-state dashes", () => {
    const empty = csatModel({ total: 0, per_day: [] });
    expect(empty.rows[1][1]).toBe("— / 5");
    expect(empty.rows[2][1]).toBe("—");
    const filled = csatModel({ total: 4, avg_rating: 4.5, positive_rate: 0.75, per_day: [{ date: "d", count: 4, avg_rating: 4.5 }] });
    expect(filled.rows[1][1]).toBe("4.50 / 5");
    expect(filled.rows[2][1]).toBe("75%");
  });

  it("formatUsd keeps sub-cent precision and collapses readable amounts", () => {
    expect(formatUsd(0.00045)).toBe("$0.000450");
    expect(formatUsd(4.321)).toBe("$4.32");
    expect(formatUsd(0)).toBe("$0.00");
    expect(formatUsd(undefined)).toBe("$0.00");
  });

  it("costSummaryRows aggregates the daily rollup into readout rows", () => {
    const rows = costSummaryRows({
      turn_count: 1234,
      prompt_tokens: 500000,
      completion_tokens: 125000,
      cost_usd: 4.321,
    });
    expect(rows).toEqual([
      ["累计推理调用", "1,234"],
      ["累计 tokens", "625,000"],
      ["累计成本", "$4.32"],
    ]);
    expect(costSummaryRows(null)).toEqual([
      ["累计推理调用", "0"],
      ["累计 tokens", "0"],
      ["累计成本", "$0.00"],
    ]);
  });

  it("costAnomalyModel distinguishes anomaly, normal and unpriced states", () => {
    const breaching = costAnomalyModel({
      anomaly: true,
      today_cost_usd: 2.5,
      baseline_cost_usd: 1.0,
      factor: 2.5,
    });
    expect(breaching.status).toBe("成本异常");
    expect(breaching.rows).toEqual([
      ["今日成本", "$2.50"],
      ["基线日均", "$1.00"],
      ["异常倍数", "2.50×"],
    ]);
    const normal = costAnomalyModel({
      anomaly: false,
      today_cost_usd: 0.5,
      baseline_cost_usd: 1.0,
      factor: 0.5,
    });
    expect(normal.status).toBe("正常");
    // W2-style empty state: nothing priced yet must read dashes, not $0.00.
    const unpriced = costAnomalyModel({ anomaly: false, today_cost_usd: 0, baseline_cost_usd: 0, factor: 0 });
    expect(unpriced.status).toBe("无定价推理");
    expect(unpriced.rows).toEqual([
      ["今日成本", "—"],
      ["基线日均", "—"],
      ["异常倍数", "—"],
    ]);
  });

  it("costBreakdownItems maps agent ids to operator labels and keeps versions verbatim", () => {
    const agents = costBreakdownItems(
      [
        { agent: "triage", turn_count: 700, cost_usd: 0.9 },
        { agent: null, turn_count: 3, cost_usd: 0.001 },
      ],
      "agent",
    );
    expect(agents.map((item) => item.label)).toEqual(["语义路由", "未归因"]);
    expect(agents[0].meta).toBe("700 次 · $0.90");
    expect(agents[1].meta).toBe("3 次 · $0.001000");
    const prompts = costBreakdownItems(
      [{ prompt_version: "support-zh v7", turn_count: 900, cost_usd: 3.5 }],
      "prompt_version",
    );
    expect(prompts[0].label).toBe("support-zh v7");
  });

  it("subscriptionRowModel formats the meta line like legacy", () => {
    const row = subscriptionRowModel(makeSubscription({ last_run_at: "2026-08-29T08:00:00Z" }));
    expect(row.title).toBe("质量报表 · 每日");
    expect(row.meta).toContain("窗口 7 天 · hook_1 · 上次 ");
    expect(row.active).toBe(true);
  });

  it("reportExportUrl clamps the window like admin-report.js", () => {
    const now = new Date(2026, 7, 29);
    expect(reportExportUrl("quality", 7, now)).toBe(
      "/api/admin/reports/quality/export?from=2026-08-23&to=2026-08-29",
    );
    expect(reportExportUrl("usage", 99, now)).toContain("from=2026-07-31");
    // 0/负值走 legacy 的 falsy→7 兜底(不是钳到 1)。
    expect(reportExportUrl("usage", 0, now)).toContain("from=2026-08-23");
  });

  it("domainsToQueryKeys maps saved domains onto namespaced queries", () => {
    const keys = domainsToQueryKeys(["quota", "members"]);
    expect(keys).toEqual([["admin", "quota"], ["admin", "members"]]);
    // Empty domains (defensive) → every admin query
    // (8 legacy + 4 cost + 4 governance incl. eval-runs).
    expect(domainsToQueryKeys([])).toHaveLength(16);
  });

  it("canManageIdentity requires the admin:manage permission", () => {
    expect(canManageIdentity({ permissions: ["admin:manage"] })).toBe(true);
    expect(canManageIdentity({ permissions: ["tenant:manage"] })).toBe(false);
    expect(canManageIdentity({ permissions: null })).toBe(false);
  });
});

describe("identity gate race", () => {
  it("recovers when helix-identity fires between render and effect subscription", async () => {
    // Production createRoot commits effects asynchronously: legacy can
    // dispatch helix-identity AFTER the island's first render (stale
    // globals snapshot) but BEFORE its effect subscription — observed live
    // as an admin island stuck on "guest" forever on a warm server. A
    // sibling that dispatches during the render pass reproduces that exact
    // window deterministically: function bodies run in tree order (island
    // first, dispatch second) while effects only run after both, so the
    // event has no subscriber yet. The post-subscribe catch-up sync must
    // recover the gate from the globals snapshot.
    stubBackend(FULL_ROUTES);
    document.documentElement.dataset.tenantId = "demo";
    function DispatchDuringRender() {
      window.__HELIX_ROLE__ = "admin";
      window.__HELIX_PERMISSIONS__ = ["admin:manage"];
      window.__HELIX_ACTOR__ = "demo.admin";
      window.dispatchEvent(
        new CustomEvent(ADMIN_EVENTS.IDENTITY, {
          detail: { role: "admin", permissions: ["admin:manage"], actorId: "demo.admin", tenantId: "demo" },
        }),
      );
      return null;
    }
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <AdminIsland />
        <DispatchDuringRender />
      </QueryClientProvider>,
    );
    await waitFor(() =>
      expect(document.getElementById(CARD_IDS.quotaReadout).textContent).toContain("演示租户"),
    );
  });
});

describe("cards render legacy contracts", () => {
  it("renders all eight cards with quota readout values", async () => {
    await renderIsland();
    expect(screen.getByText("租户配额")).toBeTruthy();
    expect(screen.getByText("成员")).toBeTruthy();
    expect(screen.getByText("Webhook")).toBeTruthy();
    expect(screen.getByText("报表订阅")).toBeTruthy();
    expect(screen.getByText("报表导出")).toBeTruthy();
    expect(screen.getByText("CSAT 评分汇总")).toBeTruthy();
    expect(screen.getByText("成本仪表盘")).toBeTruthy();
    expect(screen.getByText("治理操作台")).toBeTruthy();
    expect(screen.getByText("SLA 策略")).toBeTruthy();
    expect(screen.getByText("自动路由规则")).toBeTruthy();
    const readout = document.getElementById(CARD_IDS.quotaReadout);
    expect(readout.textContent).toContain("演示租户");
    expect(readout.textContent).toContain("64 MB");
    expect(screen.getByText("4.50 / 5")).toBeTruthy();
  });

  it("disables the self member's role select and deactivate button", async () => {
    await renderIsland();
    const selfRow = screen.getByText("demo.admin（你）").closest(".admin-member");
    expect(selfRow.querySelector(".member-role-select").disabled).toBe(true);
    expect(selfRow.querySelector(".member-deactivate").disabled).toBe(true);
    const otherRow = screen.getByText("operator-2").closest(".admin-member");
    expect(otherRow.querySelector(".member-deactivate").disabled).toBe(false);
  });

  it("resolves routing rule group names from the agent groups query", async () => {
    await renderIsland();
    expect(screen.getByText("→ 售后组 · 优先级 5")).toBeTruthy();
  });

  it("renders the governance card rows and readout", async () => {
    await renderIsland();
    const approvals = document.getElementById(CARD_IDS.governanceApprovalsList);
    expect(approvals.textContent).toContain("tool_enablement:knowledge.publish_bulk");
    expect(approvals.textContent).toContain("请求人 supervisor-1");
    const feedback = document.getElementById(CARD_IDS.governanceFeedbackList);
    expect(feedback.textContent).toContain("-1 评分 · 答非所问");
    const readout = document.getElementById(CARD_IDS.governanceDatasetsReadout);
    expect(readout.textContent).toContain("browser-feedback v1");
    expect(readout.textContent).toContain("4 条 · feedback");
  });

  it("bridges a governance decide and a feedback review to legacy", async () => {
    await renderIsland();
    const spy = bridgeSpy();
    // Approve via the pending-approvals row's 批准 button.
    const row = screen
      .getByText("tool_enablement:knowledge.publish_bulk")
      .closest(".governance-row");
    const approve = [...row.querySelectorAll("button")].find((b) => b.textContent === "批准");
    fireEvent.click(approve);
    const decided = eventsOfType(spy, ADMIN_EVENTS.GOVERNANCE_DECIDE)[0];
    expect(decided.detail).toEqual({ approvalId: "apr_gov1", approve: true });
    // Feedback review: 接受 on the single queue row.
    const feedbackRow = screen.getByText("-1 评分 · 答非所问").closest(".governance-row");
    const accept = [...feedbackRow.querySelectorAll("button")].find((b) => b.textContent === "接受");
    fireEvent.click(accept);
    const reviewed = eventsOfType(spy, ADMIN_EVENTS.GOVERNANCE_FEEDBACK_REVIEW)[0];
    expect(reviewed.detail).toEqual({ feedbackId: "fbk_gov1", accept: true });
    // The legacy answer (helix-admin-saved) drives the island's refetch.
    act(() => {
      window.dispatchEvent(
        new CustomEvent(ADMIN_EVENTS.SAVED, { detail: { ok: true, domains: ["governance-approvals"] } }),
      );
    });
  });

  it("renders the cost dashboard readouts, status and breakdowns", async () => {
    await renderIsland();
    // Status chip comes from the anomaly endpoint (2.4.0 drift semantics).
    const heading = screen.getByText("成本仪表盘").closest("h3");
    expect(heading.textContent).toContain("成本异常");
    const readout = document.getElementById(CARD_IDS.costReadout);
    expect(readout.textContent).toContain("1,234");
    expect(readout.textContent).toContain("625,000");
    expect(readout.textContent).toContain("$4.32");
    const anomaly = document.getElementById(CARD_IDS.costAnomalyReadout);
    expect(anomaly.textContent).toContain("$2.50");
    expect(anomaly.textContent).toContain("2.50×");
    const agentList = document.getElementById(CARD_IDS.costAgentList);
    expect(agentList.textContent).toContain("语义路由");
    expect(agentList.textContent).toContain("700 次 · $0.90");
    const promptList = document.getElementById(CARD_IDS.costPromptList);
    expect(promptList.textContent).toContain("support-zh v7");
    // The prompts list has a row, so its empty state must be absent.
    expect(screen.queryAllByText("暂无归因到提示版本的记录")).toHaveLength(0);
  });
});

describe("write bridge contracts", () => {
  it("bridges a quota save with backend field names and clears on saved", async () => {
    await renderIsland();
    const spy = bridgeSpy();
    fireEvent.change(document.getElementById(CARD_IDS.quotaConversations), {
      target: { value: "2500" },
    });
    fireEvent.change(document.getElementById(CARD_IDS.quotaStorageMb), {
      target: { value: "128" },
    });
    fireEvent.submit(document.getElementById(CARD_IDS.quotaForm));
    const save = eventsOfType(spy, ADMIN_EVENTS.SAVE_QUOTA)[0];
    expect(save.detail).toEqual({
      conversation_quota: 2500,
      storage_quota_bytes: 128 * 1024 * 1024,
    });
    // 桥成功回报后输入框清空(legacy saveQuota 的成功语义)。
    act(() => {
      window.dispatchEvent(
        new CustomEvent(ADMIN_EVENTS.SAVED, { detail: { ok: true, domains: ["quota"] } }),
      );
    });
    expect(document.getElementById(CARD_IDS.quotaConversations).value).toBe("");
  });

  it("bridges member invite / role change / deactivate", async () => {
    await renderIsland();
    const spy = bridgeSpy();
    fireEvent.change(document.getElementById(CARD_IDS.memberActorId), {
      target: { value: "  new-op  " },
    });
    fireEvent.change(document.getElementById(CARD_IDS.memberRole), {
      target: { value: "supervisor" },
    });
    fireEvent.submit(document.getElementById(CARD_IDS.memberForm));
    expect(eventsOfType(spy, ADMIN_EVENTS.INVITE_MEMBER)[0].detail).toEqual({
      actorId: "new-op",
      role: "supervisor",
    });

    const otherRow = screen.getByText("operator-2").closest(".admin-member");
    fireEvent.change(otherRow.querySelector(".member-role-select"), {
      target: { value: "supervisor" },
    });
    expect(eventsOfType(spy, ADMIN_EVENTS.MEMBER_ROLE)[0].detail).toEqual({
      actorId: "operator-2",
      role: "supervisor",
    });
    fireEvent.click(otherRow.querySelector(".member-deactivate"));
    expect(eventsOfType(spy, ADMIN_EVENTS.MEMBER_DEACTIVATE)[0].detail).toEqual({
      actorId: "operator-2",
    });
  });

  it("bridges webhook register with the checked events and delete with confirm left to legacy", async () => {
    await renderIsland();
    const spy = bridgeSpy();
    fireEvent.change(document.getElementById(CARD_IDS.webhookUrl), {
      target: { value: "https://example.com/new" },
    });
    fireEvent.change(document.getElementById(CARD_IDS.webhookSecret), {
      target: { value: "browser-secret-1" },
    });
    fireEvent.click(screen.getByLabelText(/会话创建/));
    fireEvent.submit(document.getElementById(CARD_IDS.webhookForm));
    const register = eventsOfType(spy, ADMIN_EVENTS.REGISTER_WEBHOOK)[0];
    expect(register.detail).toEqual({
      url: "https://example.com/new",
      secret: "browser-secret-1",
      events: ["conversation.created"],
    });

    fireEvent.click(document.querySelector(".webhook-delete"));
    expect(eventsOfType(spy, ADMIN_EVENTS.DELETE_WEBHOOK)[0].detail).toEqual({ id: "hook_1" });
  });

  it("exposes only active webhooks as subscription endpoints", async () => {
    await renderIsland({
      ...FULL_ROUTES,
      "/api/webhooks": [makeWebhook(), makeWebhook({ id: "hook_2", status: "disabled" })],
    });
    const options = [...document.getElementById(CARD_IDS.reportWebhook).options];
    expect(options.map((o) => o.value)).toEqual(["hook_1"]);
  });

  it("bridges subscription create/toggle/delete", async () => {
    await renderIsland();
    const spy = bridgeSpy();
    fireEvent.submit(document.getElementById(CARD_IDS.reportSubscriptionForm));
    expect(eventsOfType(spy, ADMIN_EVENTS.CREATE_SUBSCRIPTION)[0].detail).toEqual({
      reportType: "quality",
      schedule: "daily",
      windowDays: 7,
      webhookEndpointId: "hook_1",
    });
    fireEvent.click(document.querySelector(".report-sub-toggle"));
    expect(eventsOfType(spy, ADMIN_EVENTS.TOGGLE_SUBSCRIPTION)[0].detail).toEqual({
      id: "sub_1",
      active: false,
    });
    fireEvent.click(document.querySelector(".report-sub-delete"));
    expect(eventsOfType(spy, ADMIN_EVENTS.DELETE_SUBSCRIPTION)[0].detail).toEqual({ id: "sub_1" });
  });

  it("bridges report generate and renders the returned preview", async () => {
    await renderIsland();
    const spy = bridgeSpy();
    fireEvent.click(screen.getByRole("button", { name: "生成预览" }));
    expect(eventsOfType(spy, ADMIN_EVENTS.GENERATE_REPORT)[0].detail).toEqual({
      reportType: "quality",
      windowDays: 7,
    });
    expect(document.getElementById(CARD_IDS.reportPreview).hidden).toBe(true);
    act(() => {
      window.dispatchEvent(
        new CustomEvent(ADMIN_EVENTS.REPORT_GENERATED, {
          detail: { ok: true, text: "质量报表 2026-08-23 → 2026-08-29：3 行" },
        }),
      );
    });
    const preview = document.getElementById(CARD_IDS.reportPreview);
    expect(preview.hidden).toBe(false);
    expect(preview.textContent).toContain("3 行");
  });

  it("export routes through the submitter branch — no bridge event", async () => {
    await renderIsland();
    const spy = bridgeSpy();
    // 点击提交按钮触发隐式提交,原生事件的 submitter 指向该按钮(React
    // 合成事件不代理 submitter,岛读 nativeEvent——与 legacy 相同)。
    fireEvent.click(screen.getByRole("button", { name: "导出 CSV" }));
    // 下载契约(URL 构造/窗口钳制)由 reportExportUrl 单测覆盖;这里只证
    // 导出不走写桥、也不触发预览桥。
    expect(eventsOfType(spy, ADMIN_EVENTS.GENERATE_REPORT)).toHaveLength(0);
  });

  it("fills the SLA form from a policy then bridges the save", async () => {
    await renderIsland();
    const spy = bridgeSpy();
    fireEvent.click(document.querySelector(".sla-policy-fill"));
    expect(document.getElementById(CARD_IDS.slaPriority).value).toBe("high");
    expect(document.getElementById(CARD_IDS.slaFirstResponse).value).toBe("10");
    fireEvent.submit(document.getElementById(CARD_IDS.slaPolicyForm));
    expect(eventsOfType(spy, ADMIN_EVENTS.SAVE_SLA)[0].detail).toEqual({
      priority: "high",
      channel: "web",
      firstResponseMinutes: 10,
      resolveMinutes: 480,
    });
  });

  it("bridges routing rule create with an empty-condition omission and delete", async () => {
    await renderIsland();
    const spy = bridgeSpy();
    fireEvent.submit(document.getElementById(CARD_IDS.routingRuleForm));
    expect(eventsOfType(spy, ADMIN_EVENTS.CREATE_RULE)[0].detail).toEqual({
      intent: "",
      label: "",
      channel: "",
      groupId: "group_1",
      priority: 0,
    });
    fireEvent.click(document.querySelector(".routing-rule-delete"));
    expect(eventsOfType(spy, ADMIN_EVENTS.DELETE_RULE)[0].detail).toEqual({ id: "rule_1" });
  });
});

describe("refresh semantics", () => {
  it("refetches on a forced helix-admin-refresh", async () => {
    await renderIsland();
    const before = fetch.mock.calls.length;
    act(() => {
      window.dispatchEvent(
        new CustomEvent(ADMIN_EVENTS.REFRESH, { detail: { force: true } }),
      );
    });
    await waitFor(() => expect(fetch.mock.calls.length).toBeGreaterThan(before));
  });

  it("skips the round-trip when an unforced refresh finds fresh data", async () => {
    const { client } = await renderIsland();
    // Let every query settle first: the count below must measure only the
    // unforced refresh, not the initial mount's in-flight fetches.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 30));
    });
    const before = fetch.mock.calls.length;
    act(() => {
      window.dispatchEvent(new CustomEvent(ADMIN_EVENTS.REFRESH));
    });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(fetch.mock.calls.length).toBe(before);
  });
});
