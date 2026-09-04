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
    // Empty domains (defensive) → every admin query.
    expect(domainsToQueryKeys([])).toHaveLength(8);
  });

  it("canManageIdentity requires the admin:manage permission", () => {
    expect(canManageIdentity({ permissions: ["admin:manage"] })).toBe(true);
    expect(canManageIdentity({ permissions: ["tenant:manage"] })).toBe(false);
    expect(canManageIdentity({ permissions: null })).toBe(false);
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
    await renderIsland();
    const before = fetch.mock.calls.length;
    act(() => {
      window.dispatchEvent(new CustomEvent(ADMIN_EVENTS.REFRESH));
    });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(fetch.mock.calls.length).toBe(before);
  });
});
