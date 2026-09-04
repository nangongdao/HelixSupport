/**
 * Helix Support — quality panel & CSAT readout (ROADMAP §41.6 / ARC-001).
 *
 * Supervisor quality dashboard (buckets/gaps/charts) and the admin CSAT
 * summary. Extracted from the legacy app.js; app.js keeps thin delegating
 * wrappers with identical names/signatures, so the running UI behaviour is
 * unchanged. app.js calls configure() once at load time.
 */

let ctx = null;

/** Inject the legacy app.js singletons (state/els/api/helpers). */
export function configure(deps) {
  ctx = deps;
}

export async function loadQualityPanel() {
  // Island mode: the standalone quality view is the React island's surface
  // (yieldsLegacy), so fetching here would double every request and paint
  // the hidden legacy view containers. The inspector quality panel, though,
  // is island-rendered but legacy-fed — build the panel HTML and hand it to
  // the inspector island via helix-inspector-quality.
  if (window.__HELIX_ISLAND_MODE__) {
    await loadQualityPanelForIsland();
    return;
  }
  if (!ctx.state.me || !ctx.state.me.permissions.includes("metrics:read")) {
    if (ctx.els.qualityBuckets) {
      ctx.els.qualityBuckets.innerHTML =
        '<p class="quality-empty">当前角色无权限查看质量看板。</p>';
    }
    return;
  }
  const now = Date.now();
  // Throttle to one fetch per 10s unless explicitly refreshed.
  if (now - ctx.state.qualityLoadedAt < 10000 && ctx.state.qualityBuckets.length) {
    renderQualityPanel();
    renderQualityViewIfVisible();
    return;
  }
  ctx.state.qualityLoadedAt = now;
  try {
    // ROADMAP §17.3: the dashboard charts aggregate across dates × intents ×
    // prompt versions, so pull the widest window the endpoint allows (200)
    // instead of the 50-row default the inspector panel used before.
    const [buckets, gaps] = await Promise.all([
      ctx.api("/api/supervisor/quality?limit=200"),
      ctx.api("/api/supervisor/knowledge-gaps?limit=20"),
    ]);
    ctx.state.qualityBuckets = Array.isArray(buckets) ? buckets : [];
    ctx.state.qualityGaps = Array.isArray(gaps) ? gaps : [];
    renderQualityPanel();
    renderQualityViewIfVisible();
  } catch (error) {
    if (ctx.els.qualityBuckets) {
      ctx.els.qualityBuckets.innerHTML =
        '<p class="quality-empty">质量数据加载失败,请稍后重试。</p>';
    }
  }
}

/** Island-mode counterpart of loadQualityPanel: same fetch/throttle/permission
 * semantics, but the result is published to the inspector island instead of
 * written into the (hidden) legacy inspector containers. */
async function loadQualityPanelForIsland() {
  if (!ctx.state.me || !ctx.state.me.permissions.includes("metrics:read")) {
    publishInspectorQuality({
      bucketsHtml: '<p class="quality-empty">当前角色无权限查看质量看板。</p>',
    });
    return;
  }
  const now = Date.now();
  if (now - ctx.state.qualityLoadedAt < 10000 && ctx.state.qualityBuckets.length) {
    publishInspectorQuality();
    return;
  }
  ctx.state.qualityLoadedAt = now;
  try {
    const [buckets, gaps] = await Promise.all([
      ctx.api("/api/supervisor/quality?limit=200"),
      ctx.api("/api/supervisor/knowledge-gaps?limit=20"),
    ]);
    ctx.state.qualityBuckets = Array.isArray(buckets) ? buckets : [];
    ctx.state.qualityGaps = Array.isArray(gaps) ? gaps : [];
    publishInspectorQuality();
  } catch (error) {
    publishInspectorQuality({
      bucketsHtml: '<p class="quality-empty">质量数据加载失败,请稍后重试。</p>',
    });
  }
}

/** Hand the built quality panel HTML to the inspector island. Omitted
 * entries fall back to building from the current state. */
function publishInspectorQuality(overrides = {}) {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent(INSPECTOR_QUALITY_EVENT, {
      detail: {
        bucketsHtml:
          overrides.bucketsHtml ?? buildQualityBucketsHtml(ctx.state.qualityBuckets),
        gapsHtml:
          overrides.gapsHtml ?? buildQualityGapsHtml(ctx.state.qualityGaps),
      },
    }),
  );
}

/** Event the inspector island listens on for the quality panel HTML. */
export const INSPECTOR_QUALITY_EVENT = "helix-inspector-quality";

export function renderQualityViewIfVisible() {
  if (ctx.els.qualityView && !ctx.els.qualityView.hidden) {
    renderQualityPanel(ctx.els.qualityViewBuckets, ctx.els.qualityViewGaps);
  }
}

// ROADMAP §17.3: build the native-SVG dashboard charts from the pure
// quality-charts module; empty string when the module is not loaded yet or
// there is not enough data to draw either chart.
export function renderQualityCharts(buckets) {
  const charts = window.HelixModules?.qualityCharts;
  if (!charts) return "";
  const trend = charts.qualityTrendSvg(buckets);
  const heatmap = charts.intentVersionHeatmapSvg(buckets);
  if (!trend && !heatmap) return "";
  const trendBlock = trend
    ? `<div class="quality-chart-block"><h4>趋势</h4>${trend}</div>`
    : "";
  const heatmapBlock = heatmap
    ? `<div class="quality-chart-block"><h4>意图 × 版本</h4>${heatmap}</div>`
    : "";
  return `<section class="quality-charts" aria-label="质量图表">${trendBlock}${heatmapBlock}</section>`;
}

/** Build the buckets HTML (charts + intent cards) for the quality panel.
 * Pure given the configured escapeHtml — shared by the legacy innerHTML
 * path and the island publish path so both render identical markup. */
export function buildQualityBucketsHtml(buckets) {
  if (!buckets.length) {
    return '<p class="quality-empty">暂无质量数据。处理一些会话后会在此汇总。</p>';
  }
  // ROADMAP §17.3: native-SVG charts (trend line + intent × version
  // heatmap) from the pure quality-charts module, above the intent cards.
  const charts = renderQualityCharts(buckets);
  // Group by intent for trend/compare view.
  const byIntent = new Map();
  for (const row of buckets) {
    const list = byIntent.get(row.intent) || [];
    list.push(row);
    byIntent.set(row.intent, list);
  }
  const cards = [];
  for (const [intent, rows] of byIntent.entries()) {
    const turns = rows.reduce((sum, r) => sum + (r.turn_count || 0), 0);
    const escalations = rows.reduce(
      (sum, r) => sum + (r.escalation_count || 0),
      0,
    );
    const negatives = rows.reduce(
      (sum, r) => sum + (r.negative_feedback_count || 0),
      0,
    );
    const tokens = rows.reduce(
      (sum, r) => sum + (r.estimated_tokens || 0),
      0,
    );
    const latencies = rows
      .map((r) => r.avg_latency_ms)
      .filter((value) => typeof value === "number");
    const avgLatency = latencies.length
      ? Math.round(
          latencies.reduce((s, v) => s + v, 0) / latencies.length,
        )
      : 0;
    const escalationRate = turns
      ? ((escalations / turns) * 100).toFixed(1)
      : "0.0";
    const negativeRate = turns
      ? ((negatives / turns) * 100).toFixed(1)
      : "0.0";
    cards.push(`
      <article class="quality-card">
        <header><span class="quality-card-kicker">INTENT</span><h4>${ctx.escapeHtml(intent)}</h4></header>
        <dl class="quality-card-grid">
          <div><dt>会话数</dt><dd>${turns}</dd></div>
          <div><dt>升级率</dt><dd>${escalationRate}%</dd></div>
          <div><dt>负反馈率</dt><dd>${negativeRate}%</dd></div>
          <div><dt>平均延迟</dt><dd>${avgLatency} ms</dd></div>
          <div><dt>Token 估算</dt><dd>${tokens}</dd></div>
        </dl>
      </article>
    `);
  }
  return charts + cards.join("");
}

/** Build the knowledge-gaps HTML for the quality panel (buttons excluded —
 * the caller binds their click handlers). */
export function buildQualityGapsHtml(gaps) {
  if (!gaps.length) {
    return '<p class="quality-empty">暂无知识缺口。负反馈且无引用的回答会在此列出。</p>';
  }
  return gaps
    .map(
      (gap) => `
      <article class="quality-gap">
        <div class="quality-gap-head">
          <strong>${ctx.escapeHtml(gap.customer_name || "客户")}</strong>
          <span class="quality-gap-intent">${ctx.escapeHtml(gap.intent || "未知意图")}</span>
        </div>
        <p class="quality-gap-preview">${ctx.escapeHtml((gap.assistant_content || "").slice(0, 120))}</p>
        <button class="button button-secondary quality-gap-draft" data-conversation-id="${ctx.escapeHtml(gap.conversation_id)}" data-message-id="${ctx.escapeHtml(gap.message_id)}" type="button">
          生成知识草稿
        </button>
      </article>
    `,
    )
    .join("");
}

export function renderQualityPanel(targetBuckets = ctx.els.qualityBuckets, targetGaps = ctx.els.qualityGaps) {
  if (!targetBuckets || !targetGaps) return;
  targetBuckets.innerHTML = buildQualityBucketsHtml(ctx.state.qualityBuckets);
  targetGaps.innerHTML = buildQualityGapsHtml(ctx.state.qualityGaps);
  targetGaps
    .querySelectorAll(".quality-gap-draft")
    .forEach((button) => {
      button.addEventListener("click", () =>
        createKnowledgeDraftFromFeedback(
          button.dataset.conversationId,
          button.dataset.messageId,
        ),
      );
    });
}

export async function createKnowledgeDraftFromFeedback(conversationId, messageId) {
  try {
    const draft = await ctx.api(
      `/api/conversations/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(messageId)}/knowledge-draft`,
      { method: "POST" },
    );
    ctx.showToast(`已生成知识草稿:${draft.title}`);
  } catch (error) {
    ctx.showToast(error.message || "生成草稿失败");
  }
}

export async function loadCsatSummary() {
  if (!ctx.els.csatReadout) return;
  try {
    const data = await ctx.api("/api/admin/csat-summary");
    renderCsatSummary(data);
  } catch (error) {
    // 失败不伪装成合法的 0 态——直接提示,避免掩盖权限/端点故障(W2)。
    if (ctx.els.csatReadout) {
      ctx.els.csatReadout.innerHTML = '<dt>状态</dt><dd class="admin-empty">汇总加载失败</dd>';
    }
    if (ctx.els.csatTrend) ctx.els.csatTrend.innerHTML = "";
  }
}

export function renderCsatSummary(data) {
  if (!ctx.els.csatReadout || !ctx.els.csatTrend) return;
  const days = Array.isArray(data.per_day) ? data.per_day : [];
  const total = Math.round(Number(data.total || 0));
  // 有样本才有均值/好评率;空态用 — 而非 0.00(客户不可能打 0 分,W2)。
  const avg = total > 0 ? `${Number(data.avg_rating || 0).toFixed(2)} / 5` : "— / 5";
  const pct = total > 0 ? `${Math.round(Number(data.positive_rate || 0) * 100)}%` : "—";
  ctx.els.csatReadout.innerHTML = `
    <dt>累计样本数</dt><dd>${total}</dd>
    <dt>累计平均分</dt><dd>${avg}</dd>
    <dt>累计好评率</dt><dd>${pct}</dd>`;
  if (!days.length) {
    ctx.els.csatTrend.innerHTML = '<li class="admin-empty">暂无已回收的评分</li>';
    return;
  }
  ctx.els.csatTrend.innerHTML = days
    .map(
      (d) => `
      <li class="csat-day">
        <span class="csat-day-date">${ctx.escapeHtml(d.date)}</span>
        <span class="csat-day-meta">${d.count} 份 · 平均 ${Number(d.avg_rating || 0).toFixed(2)}</span>
      </li>`,
    )
    .join("");
}

/**
 * Bind the quality/CSAT refresh controls (exactly once, at app.js load).
 */
export function bindQuality() {
  if (!ctx?.els) return false;
  if (ctx.els.refreshQuality) {
    ctx.els.refreshQuality.addEventListener("click", () => {
      ctx.state.qualityLoadedAt = 0;
      loadQualityPanel();
    });
  }
  if (ctx.els.refreshQualityView) {
    ctx.els.refreshQualityView.addEventListener("click", () => {
      // Island mode: the header button is not yielded (outside the island
      // mount) — bridge the forced refresh to the island's query.
      if (window.__HELIX_ISLAND_MODE__) {
        window.dispatchEvent(
          new CustomEvent("helix-quality-refresh", { detail: { force: true } }),
        );
        return;
      }
      ctx.state.qualityLoadedAt = 0;
      void loadQualityPanel();
      renderQualityPanel(ctx.els.qualityViewBuckets, ctx.els.qualityViewGaps);
    });
  }
  // Island mode: the inspector island renders the quality gaps (fed via
  // helix-inspector-quality); its 生成知识草稿 buttons delegate the write
  // back here so api()/toast stay in one place.
  if (typeof window !== "undefined") {
    window.addEventListener("helix-quality-draft", (event) => {
      const { conversationId, messageId } = event.detail || {};
      if (conversationId && messageId) {
        void createKnowledgeDraftFromFeedback(conversationId, messageId);
      }
    });
  }
  return true;
}