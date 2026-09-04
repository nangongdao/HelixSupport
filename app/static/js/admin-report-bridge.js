/**
 * Helix Support — admin report/SLA/routing island bridges (app.js <500 campaign).
 *
 * The report-subscription, report-generation, SLA-policy and routing-rule
 * write bridges for the React admin island. The island dispatches helix-admin-*
 * events; these handlers own the api()/toast lifecycle and answer with
 * helix-admin-saved (refetch) / helix-admin-report-generated (preview) events.
 *
 * Extracted from app.js; api/showToast arrive through configure. The legacy
 * form equivalents already live in js/admin-report.js.
 */

let ctx = null;

// Report preview labels, shared verbatim with js/admin-report.js
// generateReportPreview (quality/usage).
const REPORT_TYPE_LABELS = { quality: "质量报表", usage: "使用量报表" };

/** Inject the legacy app.js singletons (api/showToast). */
export function configure(deps) {
  ctx = deps;
}

/** Emit the island's refetch signal for the given admin domains. */
function dispatchAdminSaved(ok, domains) {
  window.dispatchEvent(new CustomEvent("helix-admin-saved", { detail: { ok, domains } }));
}

export async function createSubscriptionFromIsland({ reportType, schedule, windowDays, webhookEndpointId } = {}) {
  if (!webhookEndpointId) {
    ctx.showToast("请先选择 Webhook 端点", true);
    return;
  }
  let ok = false;
  try {
    await ctx.api("/api/admin/report-subscriptions", {
      method: "POST",
      body: JSON.stringify({
        report_type: reportType || "quality",
        schedule: schedule || "daily",
        window_days: Number(windowDays || 7),
        webhook_endpoint_id: webhookEndpointId,
      }),
    });
    ok = true;
    ctx.showToast("报表订阅已创建");
  } catch (error) {
    ctx.showToast(`创建订阅失败：${error.message || error}`, true);
  } finally {
    dispatchAdminSaved(ok, ["subscriptions"]);
  }
}

export async function toggleSubscriptionFromIsland({ id, active } = {}) {
  if (!id) return;
  let ok = false;
  try {
    await ctx.api(`/api/admin/report-subscriptions/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify({ active: Boolean(active) }),
    });
    ok = true;
  } catch (error) {
    ctx.showToast(`订阅状态变更失败：${error.message || error}`, true);
  } finally {
    dispatchAdminSaved(ok, ["subscriptions"]);
  }
}

export async function deleteSubscriptionFromIsland({ id } = {}) {
  if (!id) return;
  let ok = false;
  try {
    await ctx.api(`/api/admin/report-subscriptions/${encodeURIComponent(id)}`, { method: "DELETE" });
    ok = true;
    ctx.showToast("订阅已删除");
  } catch (error) {
    ctx.showToast(`删除订阅失败：${error.message || error}`, true);
  } finally {
    dispatchAdminSaved(ok, ["subscriptions"]);
  }
}

export async function generateReportFromIsland({ reportType, windowDays } = {}) {
  const type = reportType || "quality";
  try {
    const report = await ctx.api("/api/admin/reports/generate", {
      method: "POST",
      body: JSON.stringify({ report_type: type, window_days: Number(windowDays || 7) }),
    });
    // 预览文案与 js/admin-report.js generateReportPreview 逐字一致。
    const rows = Array.isArray(report.rows) ? report.rows : [];
    const header = `${REPORT_TYPE_LABELS[type] || type} ${report.from_date} → ${report.to_date}：${rows.length} 行`;
    const text = rows.length
      ? `${header}\n${rows.slice(0, 5).map((row) => JSON.stringify(row)).join("\n")}`
      : `${header}\n（窗口内暂无数据）`;
    window.dispatchEvent(new CustomEvent("helix-admin-report-generated", { detail: { ok: true, text } }));
  } catch (error) {
    ctx.showToast(`报表生成失败：${error.message || error}`, true);
    window.dispatchEvent(new CustomEvent("helix-admin-report-generated", { detail: { ok: false } }));
  }
}

export async function saveSlaFromIsland({ priority, channel, firstResponseMinutes, resolveMinutes } = {}) {
  const firstResponse = Number(firstResponseMinutes || 0);
  const resolve = Number(resolveMinutes || 0);
  if (!firstResponse || !resolve) {
    ctx.showToast("请填写首响与解决时限", true);
    return;
  }
  let ok = false;
  try {
    await ctx.api("/api/admin/sla-policies", {
      method: "PUT",
      body: JSON.stringify({
        priority: priority || null,
        channel: (channel || "").trim() || null,
        first_response_minutes: firstResponse,
        resolve_minutes: resolve,
      }),
    });
    ok = true;
    ctx.showToast("SLA 策略已保存");
  } catch (error) {
    ctx.showToast(`SLA 策略保存失败：${error.message || error}`, true);
  } finally {
    dispatchAdminSaved(ok, ["sla"]);
  }
}

export async function createRuleFromIsland({ intent, label, channel, groupId, priority } = {}) {
  if (!groupId) {
    ctx.showToast("请先选择分配组", true);
    return;
  }
  const body = { group_id: groupId, priority: Number(priority || 0) };
  for (const [key, value] of [["intent", intent], ["label", label], ["channel", channel]]) {
    const trimmed = (value || "").trim();
    if (trimmed) body[key] = trimmed;
  }
  let ok = false;
  try {
    await ctx.api("/api/admin/routing-rules", { method: "POST", body: JSON.stringify(body) });
    ok = true;
    ctx.showToast("路由规则已添加");
  } catch (error) {
    ctx.showToast(`路由规则添加失败：${error.message || error}`, true);
  } finally {
    dispatchAdminSaved(ok, ["rules"]);
  }
}

export async function deleteRuleFromIsland({ id } = {}) {
  if (!id) return;
  let ok = false;
  try {
    await ctx.api(`/api/admin/routing-rules/${encodeURIComponent(id)}`, { method: "DELETE" });
    ok = true;
    ctx.showToast("路由规则已删除");
  } catch (error) {
    ctx.showToast(`路由规则删除失败：${error.message || error}`, true);
  } finally {
    dispatchAdminSaved(ok, ["rules"]);
  }
}

export default {
  createSubscriptionFromIsland,
  toggleSubscriptionFromIsland,
  deleteSubscriptionFromIsland,
  generateReportFromIsland,
  saveSlaFromIsland,
  createRuleFromIsland,
  deleteRuleFromIsland,
};