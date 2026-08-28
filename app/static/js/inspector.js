/**
 * Helix Support — inspector domain module (ROADMAP §43.6 / ARC-001 第二步).
 *
 * The right-hand inspector surface: overview / evidence / audit tab
 * renderers plus the label & priority mutation flows embedded in the
 * overview tab. Extracted from the legacy app.js; app.js keeps thin
 * delegating wrappers with identical names/signatures, so the running UI
 * behaviour is unchanged. app.js calls configure() once at load time.
 *
 * Component contract (the pure part runs under `node --test`):
 * - INSPECTOR_TABS       the four tab mount points in display order
 *                        ("quality" hosts the Phase 21 supervisor panel).
 * - createInspectorState initial machine state (first tab, nothing drawn).
 * - reduceInspector      the only defined transitions: select-tab /
 *                        mark-rendered / invalidate / set-collapsed. The DOM
 *                        layer still drives the legacy `state.inspectorRendered`
 *                        flags for behavioural parity during migration; this
 *                        reducer mirrors those semantics and becomes the
 *                        single source once app.js state moves into modules.
 */

let ctx = null;

/** Inject the legacy app.js singletons (state/els/api/helpers). */
export function configure(deps) {
  ctx = deps;
  bindIslandBridge();
}

/** Tab mount points in display order. */
export const INSPECTOR_TABS = Object.freeze(["overview", "evidence", "audit", "quality"]);

/** Tabs whose body renders from detail data (the quality tab self-fetches). */
const RENDERED_TABS = ["overview", "evidence", "audit"];

/** Initial inspector tab-machine state. */
export function createInspectorState() {
  return {
    activeTab: "overview",
    collapsed: false,
    rendered: { overview: false, evidence: false, audit: false },
  };
}

/**
 * Pure tab-machine reducer. Unknown actions and no-op transitions return the
 * same object reference so callers can cheaply detect "nothing changed".
 * @param {ReturnType<typeof createInspectorState>} state
 * @param {{type: string, tab?: string, collapsed?: boolean}} action
 */
export function reduceInspector(state, action) {
  switch (action.type) {
    case "select-tab": {
      if (!INSPECTOR_TABS.includes(action.tab)) return state;
      return { ...state, activeTab: action.tab };
    }
    case "mark-rendered": {
      if (!RENDERED_TABS.includes(action.tab) || state.rendered[action.tab]) return state;
      return {
        ...state,
        rendered: { ...state.rendered, [action.tab]: true },
      };
    }
    case "invalidate": {
      const untouched =
        !state.rendered.overview && !state.rendered.evidence && !state.rendered.audit;
      if (untouched) return state;
      return { ...state, rendered: { overview: false, evidence: false, audit: false } };
    }
    case "set-collapsed": {
      if (typeof action.collapsed !== "boolean" || action.collapsed === state.collapsed) {
        return state;
      }
      return { ...state, collapsed: action.collapsed };
    }
    default:
      return state;
  }
}

/**
 * Only `/`-relative and https:// citation targets are linkable; anything
 * else (and anything empty) degrades to a dead anchor instead of an
 * attacker-controlled href.
 * @param {string|undefined} value
 * @returns {string}
 */
export function safeCitationUrl(value) {
  const url = String(value || "");
  if (url.startsWith("/") || url.startsWith("https://")) return url;
  return "#";
}

export async function updateLabels(event) {
  event.preventDefault();
  if (!ctx.state.selectedId) return;
  const form = event.currentTarget;
  const input = form.elements.labels;
  const labels = input.value
    .split(/[,，]/)
    .map((label) => label.trim())
    .filter(Boolean);
  const conversationId = ctx.state.selectedId;
  ctx.setFormBusy(form, true);
  try {
    await ctx.api(`/api/conversations/${encodeURIComponent(conversationId)}/labels`, {
      method: "PUT",
      body: JSON.stringify({ labels }),
    });
    ctx.state.labelsLoadedAt = 0;
    ctx.showToast("标签已更新");
    await ctx.loadDetail(conversationId);
    await ctx.refreshAll({ silent: true, refreshDetail: false });
  } catch (error) {
    ctx.showToast(error.message, true);
  } finally {
    ctx.setFormBusy(form, false);
  }
}

export async function updatePriority(button) {
  if (!ctx.state.selectedId || button.getAttribute("aria-pressed") === "true") return;
  const conversationId = ctx.state.selectedId;
  const priority = button.dataset.priority;
  ctx.els.inspectorOverview.querySelectorAll(".priority-option").forEach((option) => {
    option.disabled = true;
  });
  try {
    await ctx.api(`/api/conversations/${encodeURIComponent(conversationId)}`, {
      method: "PATCH",
      body: JSON.stringify({ priority }),
    });
    ctx.showToast(priority === "high" ? "已提升为高优先级" : "已调整为普通优先级");
    await ctx.loadDetail(conversationId);
    void ctx.refreshAll({ silent: true, refreshDetail: false });
  } catch (error) {
    ctx.showToast(error.message, true);
    if (ctx.state.selectedId === conversationId) await ctx.loadDetail(conversationId);
  }
}

export function renderOverview(detail) {
  const conversation = detail.conversation;
  const labels = conversation.labels || [];
  const assistant = ctx.latestAssistant(detail.messages);
  const metadata = assistant?.metadata || {};
  const confidence = Number.isFinite(Number(metadata.confidence))
    ? Math.round(Number(metadata.confidence) * 100)
    : 0;
  const flags = metadata.risk_categories || [];
  const toolCalls = metadata.tool_calls || [];
  ctx.els.inspectorOverview.innerHTML = `
    <section class="inspector-section">
      <h3>会话</h3>
      <dl class="detail-list">
        <div class="detail-row"><dt>会话 ID</dt><dd>${ctx.escapeHtml(conversation.id)}</dd></div>
        <div class="detail-row"><dt>渠道</dt><dd>${ctx.escapeHtml(conversation.channel)}</dd></div>
        <div class="detail-row"><dt>客户标识</dt><dd>${ctx.escapeHtml(conversation.customer_ref || "未绑定")}</dd></div>
        <div class="detail-row"><dt>优先级</dt><dd>
          <div class="priority-control" role="group" aria-label="调整会话优先级">
            <button class="priority-option normal${conversation.priority === "normal" ? " is-active" : ""}" type="button" data-priority="normal" aria-pressed="${conversation.priority === "normal"}">普通</button>
            <button class="priority-option high${conversation.priority === "high" ? " is-active" : ""}" type="button" data-priority="high" aria-pressed="${conversation.priority === "high"}">高</button>
          </div>
        </dd></div>
        <div class="detail-row"><dt>标签</dt><dd><div class="overview-labels">${ctx.renderLabelChips(labels)}</div></dd></div>
        <div class="detail-row"><dt>分配</dt><dd>${ctx.escapeHtml(conversation.assigned_agent || "未分配")}</dd></div>
        <div class="detail-row"><dt>认领</dt><dd>${conversation.claim_active ? `${ctx.escapeHtml(conversation.claimed_by)} · 至 ${ctx.escapeHtml(ctx.formatTime(conversation.claim_expires_at, true))}` : "未认领"}</dd></div>
      </dl>
      ${ctx.canOperate() ? `<form id="conversationLabelsForm" class="label-editor">
        <label class="label-editor-field">
          <svg class="icon"><use href="/static/icons.svg?v=1.4.0#tag" /></svg>
          <span class="sr-only">会话标签</span>
          <input name="labels" type="text" maxlength="240" value="${ctx.escapeHtml(labels.join(", "))}" placeholder="VIP, 退款风险" />
        </label>
        <button type="submit" title="保存标签" aria-label="保存标签">
          <svg class="icon"><use href="/static/icons.svg?v=1.4.0#check" /></svg>
        </button>
      </form>` : ""}
    </section>
    <section class="inspector-section">
      <h3>最近一次 Agent 运行</h3>
      ${assistant ? `
        <dl class="detail-list">
          <div class="detail-row"><dt>Agent</dt><dd>${ctx.escapeHtml(metadata.agent || "-")}</dd></div>
          <div class="detail-row"><dt>意图</dt><dd>${ctx.escapeHtml(metadata.intent || "-")}</dd></div>
          <div class="detail-row"><dt>路由模式</dt><dd>${ctx.escapeHtml(metadata.route_mode || "-")}</dd></div>
          <div class="detail-row"><dt>质量门</dt><dd>${metadata.quality_approved ? "通过" : "转人工"}</dd></div>
          <div class="detail-row"><dt>置信度</dt><dd>${confidence}%</dd></div>
        </dl>
        <progress class="confidence-progress" max="100" value="${confidence}">${confidence}%</progress>
      ` : '<div class="inspector-empty">尚无 Agent 运行记录</div>'}
    </section>
    ${flags.length ? `<section class="inspector-section"><h3>风险标签</h3><div class="trace-flags">${flags.map((flag) => `<span class="trace-flag">${ctx.escapeHtml(flag)}</span>`).join("")}</div></section>` : ""}
    ${toolCalls.length ? `<section class="inspector-section"><h3>工具执行</h3><dl class="detail-list">${toolCalls.map((call) => `<div class="detail-row"><dt>${ctx.escapeHtml(call.tool)}</dt><dd>${ctx.escapeHtml(call.code)} · ${ctx.escapeHtml(call.duration_ms)} ms</dd></div>`).join("")}</dl></section>` : ""}
  `;
  ctx.els.inspectorOverview.querySelectorAll(".priority-option").forEach((button) => {
    button.addEventListener("click", () => updatePriority(button));
  });
  const labelsForm = ctx.els.inspectorOverview.querySelector("#conversationLabelsForm");
  if (labelsForm) labelsForm.addEventListener("submit", updateLabels);
}

export function renderEvidence(detail) {
  const assistant = ctx.latestAssistant(detail.messages);
  const citations = assistant?.metadata?.citations || [];
  if (!citations.length) {
    ctx.els.inspectorEvidence.innerHTML = '<div class="inspector-empty">本次回答没有知识引用</div>';
    return;
  }
  ctx.els.inspectorEvidence.innerHTML = `
    <section class="inspector-section">
      <h3>已批准知识来源</h3>
      ${citations
        .map((citation) => {
          const href = safeCitationUrl(citation.url);
          const external = href.startsWith("https://") ? ' target="_blank" rel="noreferrer"' : "";
          return `<a class="citation-item" href="${ctx.escapeHtml(href)}"${external}>
            <span class="citation-title">${ctx.escapeHtml(citation.title || citation.id)}</span>
            <span class="citation-meta">${ctx.escapeHtml(citation.url || "内部知识")} · v${ctx.escapeHtml(citation.version || "-")}</span>
          </a>`;
        })
        .join("")}
    </section>`;
}

export function renderAudit(detail) {
  if (!detail.audit_events.length) {
    ctx.els.inspectorAudit.innerHTML = '<div class="inspector-empty">暂无审计事件</div>';
    return;
  }
  const events = ctx.state.lowPerf ? detail.audit_events.slice(-20) : detail.audit_events;
  const compactPayload = ctx.state.lowPerf;
  ctx.els.inspectorAudit.innerHTML = `<div class="audit-list">${events
    .map(
      (event) => `
        <article class="audit-item">
          <div class="audit-meta"><strong>${ctx.escapeHtml(event.event_type)}</strong><time datetime="${ctx.escapeHtml(event.created_at)}">${ctx.escapeHtml(ctx.formatTime(event.created_at, true))}</time></div>
          <div class="audit-actor">${ctx.escapeHtml(event.actor)}${event.request_id ? ` · ${ctx.escapeHtml(event.request_id)}` : ""}</div>
          ${compactPayload ? "" : `<pre class="audit-payload">${ctx.escapeHtml(JSON.stringify(event.payload, null, 2))}</pre>`}
        </article>`,
    )
    .join("")}</div>`;
}

export function resetInspectorRenderFlags() {
  ctx.state.inspectorRendered = { overview: false, evidence: false, audit: false };
}

export function ensureInspectorTab(tab, detail = ctx.state.detail) {
  if (!detail || ctx.state.inspectorCollapsed) return;
  if (tab === "overview" && !ctx.state.inspectorRendered.overview) {
    renderOverview(detail);
    ctx.state.inspectorRendered.overview = true;
  } else if (tab === "evidence" && !ctx.state.inspectorRendered.evidence) {
    renderEvidence(detail);
    ctx.state.inspectorRendered.evidence = true;
  } else if (tab === "audit" && !ctx.state.inspectorRendered.audit) {
    renderAudit(detail);
    ctx.state.inspectorRendered.audit = true;
  }
}

export function renderInspector(detail) {
  resetInspectorRenderFlags();
  if (ctx.state.inspectorCollapsed) {
    ctx.els.inspectorOverview.innerHTML = "";
    ctx.els.inspectorEvidence.innerHTML = "";
    ctx.els.inspectorAudit.innerHTML = "";
    return;
  }
  // D3 island mode: the React inspector island owns the tab + panel DOM.
  // Publish the detail snapshot it mirrors and stop painting the legacy
  // panels (kept hidden by the loader) — the tab state stays in legacy.
  if (typeof window !== "undefined" && window.__HELIX_ISLAND_MODE__) {
    publishInspectorState();
    return;
  }
  ensureInspectorTab(ctx.state.activeTab, detail);
  switchInspectorTab(ctx.state.activeTab);
}

/** Publish the current inspector state for the React island mirror. */
function publishInspectorState() {
  window.dispatchEvent(
    new CustomEvent("helix-inspector-state", {
      detail: {
        detail: ctx.state.detail,
        collapsed: ctx.state.inspectorCollapsed,
        activeTab: ctx.state.activeTab,
        canOperate: ctx.canOperate(),
      },
    }),
  );
}

// D3 bridge: the React inspector island dispatches tab switches, priority
// changes and label saves (the legacy tabs/panels are yielded + hidden in
// the desktop shell). Route them back to this module's handlers.
function bindIslandBridge() {
  if (typeof window === "undefined") return;
  window.addEventListener("helix-inspector-sync", () => publishInspectorState());
  window.addEventListener("helix-inspector-tab", (event) => {
    const { tab } = event.detail || {};
    if (!tab || !INSPECTOR_TABS.includes(tab)) return;
    ctx.state.activeTab = tab;
    publishInspectorState();
    if (tab === "quality") ctx.loadQualityPanel?.();
  });
  window.addEventListener("helix-inspector-priority", (event) => {
    const { priority } = event.detail || {};
    if (!priority) return;
    const button = ctx.els.inspectorOverview?.querySelector?.(
      `.priority-option[data-priority="${priority}"]`,
    );
    if (button) void updatePriority(button);
  });
  window.addEventListener("helix-inspector-labels", (event) => {
    const { labels } = event.detail || {};
    if (!Array.isArray(labels)) return;
    const form = {
      preventDefault: () => {},
      elements: { labels: { value: labels.join(", ") } },
    };
    void updateLabels(form);
  });
}

export function switchInspectorTab(tab) {
  ctx.state.activeTab = tab;
  document.querySelectorAll(".inspector-tab").forEach((button) => {
    const active = button.dataset.tab === tab;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-selected", String(active));
  });
  ensureInspectorTab(tab);
  ctx.els.inspectorOverview.hidden = tab !== "overview";
  ctx.els.inspectorEvidence.hidden = tab !== "evidence";
  ctx.els.inspectorAudit.hidden = tab !== "audit";
  if (ctx.els.qualityPanel) {
    ctx.els.qualityPanel.hidden = tab !== "quality";
    if (tab === "quality") {
      ctx.loadQualityPanel();
    }
  }
}

export default {
  INSPECTOR_TABS,
  createInspectorState,
  reduceInspector,
  safeCitationUrl,
  configure,
  updateLabels,
  updatePriority,
  renderOverview,
  renderEvidence,
  renderAudit,
  resetInspectorRenderFlags,
  ensureInspectorTab,
  renderInspector,
  switchInspectorTab,
};
