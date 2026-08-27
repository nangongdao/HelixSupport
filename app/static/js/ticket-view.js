/**
 * Helix Support — ticket view (ROADMAP §41.6 / ARC-001).
 *
 * Ticket list/detail/state machine and the workspace queue/tickets tab.
 * Extracted from the legacy app.js; app.js keeps thin delegating wrappers
 * with identical names/signatures, so the running UI behaviour is unchanged.
 * app.js calls configure() once at load time with its singletons.
 */

let ctx = null;

/** Inject the legacy app.js singletons (state/els/api/helpers). */
export function configure(deps) {
  ctx = deps;
}

const TICKET_STATUS_NAMES = { open: "待处理", in_progress: "处理中", closed: "已关闭" };
let ticketBadgeCache = {};

export async function enrichTicketBadge(ticketId) {
  if (!ticketId || !ctx.els.ticketBadge) return;
  if (ticketBadgeCache[ticketId]) {
    ctx.els.ticketBadge.textContent = `工单 ${ticketId} · ${ticketBadgeCache[ticketId]}`;
    return;
  }
  try {
    const ticket = await ctx.api(`/api/tickets/${encodeURIComponent(ticketId)}`);
    const status = TICKET_STATUS_NAMES[ticket.status] || ticket.status || "";
    ticketBadgeCache[ticketId] = status;
    if (ctx.state.selectedId && ctx.els.ticketBadge) {
      ctx.els.ticketBadge.textContent = `工单 ${ticketId} · ${status}`;
    }
  } catch {
    /* badge stays at the id-only form */
  }
}

export async function convertToTicket() {
  const conversationId = ctx.state.selectedId;
  if (!conversationId || !ctx.canOperate()) return;
  const subject = window.prompt("转工单主题（长周期问题描述）", "问题跟进");
  if (subject === null || !subject.trim()) return;
  try {
    await ctx.api("/api/tickets", {
      method: "POST",
      body: JSON.stringify({ conversation_id: conversationId, subject: subject.trim() }),
    });
    await ctx.loadDetail(conversationId);
  } catch (error) {
    window.alert(`转工单失败：${error.message || error}`);
  }
}

let ticketListLoaded = false;
// 工单详情「跳转关联会话」窗口内抑制 runRefresh 自动选第一条会话的计数标志
// (jumpToTicketConversation 独占);>0 时 else-if 的 auto-select 被跳过。
let suppressAutoSelect = 0;
let activeTicketId = null;

export function switchWorkspaceTab(field) {
  if (!ctx.els.wsTabQueue || !ctx.els.wsTabTickets) return;
  const isTickets = field === "tickets";
  if (ctx.els.queuePane) ctx.els.queuePane.dataset.mode = isTickets ? "tickets" : "queue";
  if (ctx.els.wsTabQueue) {
    ctx.els.wsTabQueue.classList.toggle("is-active", !isTickets);
    ctx.els.wsTabQueue.setAttribute("aria-selected", String(!isTickets));
  }
  if (ctx.els.wsTabTickets) {
    ctx.els.wsTabTickets.classList.toggle("is-active", isTickets);
    ctx.els.wsTabTickets.setAttribute("aria-selected", String(isTickets));
  }
  if (ctx.els.ticketPane) ctx.els.ticketPane.hidden = !isTickets;
  if (isTickets) {
    if (!ticketListLoaded) void loadTickets();
    else refreshTicketsList();
  } else {
    closeTicketDetail();
    void ctx.refreshAll({ silent: true });
  }
}

/** Hide the ticket detail, restoring the conversation/empty mount point. */
export function closeTicketDetail() {
  if (activeTicketId === null) return;
  activeTicketId = null;
  if (ctx.els.ticketDetailView) ctx.els.ticketDetailView.hidden = true;
  if (ctx.state.selectedId && ctx.els.conversationView) {
    ctx.els.conversationView.hidden = false;
    if (ctx.els.emptyState) ctx.els.emptyState.hidden = true;
  } else {
    if (ctx.els.conversationView) ctx.els.conversationView.hidden = true;
    if (ctx.els.emptyState) ctx.els.emptyState.hidden = false;
  }
}

export async function loadTickets() {
  // D3 take-over: in the desktop shell the React ticket island owns the list
  // (#ticketList is yielded and hidden by the island loader). Rendering into a
  // hidden container would still duplicate .ticket-row nodes in the DOM and
  // fire a redundant fetch, so yield here instead.
  if (typeof window !== "undefined" && window.__HELIX_ISLAND_MODE__) return;
  const status = ctx.els.ticketStatusFilter?.value || "";
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  try {
    const tickets = await ctx.api(`/api/tickets${query}`);
    ticketListLoaded = true;
    renderTicketList(tickets || []);
  } catch (error) {
    if (ctx.els.ticketList) {
      ctx.els.ticketList.innerHTML = `<p class="ticket-empty">加载失败：${ctx.escapeHtml(error.message || error)}</p>`;
    }
  }
}

export function refreshTicketsList() {
  void loadTickets();
}

export function renderTicketList(tickets) {
  if (!ctx.els.ticketList) return;
  if (!tickets.length) {
    ctx.els.ticketList.innerHTML = `<p class="ticket-empty">暂无工单</p>`;
    return;
  }
  ctx.els.ticketList.innerHTML = tickets
    .map((t) => {
      const statusName = TICKET_STATUS_NAMES[t.status] || t.status;
      const high = t.priority === "high" ? " is-high" : "";
      const ref = t.customer_ref ? ` · ${ctx.escapeHtml(t.customer_ref)}` : "";
      return `<button type="button" class="ticket-row" data-ticket-id="${ctx.escapeHtml(t.id)}">
        <span class="ticket-row-main">
          <span class="ticket-subject">${ctx.escapeHtml(t.subject)}</span>
          <span class="ticket-meta">${ctx.escapeHtml(t.customer_name || "—")}${ref}</span>
        </span>
        <span class="ticket-row-side">
          <span class="priority-pill${high}">${t.priority === "high" ? "高优" : "普通"}</span>
          <span class="status-pill">${ctx.escapeHtml(statusName)}</span>
          <span class="ticket-meta">${ctx.escapeHtml(ctx.formatTime(t.updated_at))}</span>
        </span>
      </button>`;
    })
    .join("");
}

export async function openTicketDetail(ticketId) {
  activeTicketId = ticketId;
  try {
    const detail = await ctx.api(`/api/tickets/${encodeURIComponent(ticketId)}`);
    // 过期响应守卫:await 期间用户已切换另一工单或关闭详情(activeTicketId
    // 变化),旧票响应不得覆盖当前视图,也不得把 null 传给 /api/tickets/null。
    if (activeTicketId !== ticketId) return;
    renderTicketDetail(detail);
    if (ctx.els.ticketDetailView) ctx.els.ticketDetailView.hidden = false;
    if (ctx.els.conversationView) ctx.els.conversationView.hidden = true;
    if (ctx.els.emptyState) ctx.els.emptyState.hidden = true;
  } catch (error) {
    if (activeTicketId === ticketId) {
      activeTicketId = null;
      window.alert(`工单加载失败：${error.message || error}`);
    }
  }
}

export function renderTicketDetail(t) {
  const statusName = TICKET_STATUS_NAMES[t.status] || t.status;
  if (ctx.els.ticketDetailTitle) ctx.els.ticketDetailTitle.textContent = `${t.id} · ${t.subject}`;
  if (ctx.els.ticketDetailStatus) {
    ctx.els.ticketDetailStatus.textContent = statusName;
    ctx.els.ticketDetailStatus.classList.toggle("is-ticket-closed", t.status === "closed");
  }
  if (ctx.els.ticketDetailPriority) {
    ctx.els.ticketDetailPriority.textContent = t.priority === "high" ? "高优先级" : "普通优先级";
    ctx.els.ticketDetailPriority.classList.toggle("is-high", t.priority === "high");
    ctx.els.ticketDetailPriority.hidden = false;
  }
  if (ctx.els.ticketDetailSubtitle) {
    const parts = [t.customer_name || "—"];
    if (t.customer_ref) parts.push(t.customer_ref);
    if (t.assigned_agent) parts.push(`负责人：${t.assigned_agent}`);
    parts.push(`创建 ${ctx.formatTime(t.created_at)}`);
    if (t.closed_at) parts.push(`关闭 ${ctx.formatTime(t.closed_at)}`);
    ctx.els.ticketDetailSubtitle.textContent = parts.join(" · ");
  }
  if (ctx.els.ticketDetailDescription) {
    if (t.description) {
      ctx.els.ticketDetailDescription.hidden = false;
      ctx.els.ticketDetailDescription.textContent = t.description;
    } else {
      ctx.els.ticketDetailDescription.hidden = true;
    }
  }
  renderTicketConvs(t.conversations);
  renderTicketTransitions(t.status);
  const linkedThisConv = (t.conversations || []).some((c) => c.id === ctx.state.selectedId);
  if (ctx.els.ticketLinkCurrent) ctx.els.ticketLinkCurrent.hidden = !ctx.state.selectedId || linkedThisConv;
}

export function renderTicketTransitions(status) {
  if (!ctx.els.ticketTransitions) return;
  const actions = {
    open: [["in_progress", "开始处理"], ["closed", "直接关闭"]],
    in_progress: [["closed", "关闭"]],
    closed: [["open", "重开"]],
  }[status] || [];
  ctx.els.ticketTransitions.innerHTML = actions
    .map(
      ([next, label]) =>
        `<button type="button" class="button button-secondary ticket-transition" data-status="${ctx.escapeHtml(next)}">${ctx.escapeHtml(label)}</button>`,
    )
    .join("");
}

export function renderTicketConvs(convs) {
  if (!ctx.els.ticketDetailConvs) return;
  if (!convs || !convs.length) {
    ctx.els.ticketDetailConvs.innerHTML = `<h3 class="ticket-convs-title">关联会话</h3><p class="ticket-empty">未关联会话</p>`;
    return;
  }
  ctx.els.ticketDetailConvs.innerHTML =
    `<h3 class="ticket-convs-title">关联会话</h3>` +
    convs
      .map(
        (c) =>
          `<button type="button" class="ticket-conv-row" data-conv-id="${ctx.escapeHtml(c.id)}">
            <span class="ticket-meta">${ctx.escapeHtml(c.customer_name || "—")}</span>
            <span class="status-pill">${ctx.escapeHtml(ctx.statusLabel(c.status))}</span>
            <span class="ticket-meta">${ctx.escapeHtml(ctx.formatTime(c.updated_at))}</span>
          </button>`,
      )
      .join("");
}

export async function transitionActiveTicket(status) {
  // 入口拷贝:await 期间用户可能已关闭详情或切到另一工单,不能读模块级变量。
  const ticketId = activeTicketId;
  if (!ticketId) return;
  try {
    await ctx.api(`/api/tickets/${encodeURIComponent(ticketId)}/transition`, {
      method: "POST",
      body: JSON.stringify({ status }),
    });
    void openTicketDetail(ticketId);
    void refreshTicketsList();
  } catch (error) {
    window.alert(`工单状态变更失败：${error.message || error}`);
  }
}

export async function linkActiveTicketConversation() {
  const ticketId = activeTicketId;
  const conversationId = ctx.state.selectedId;
  if (!ticketId || !conversationId) return;
  try {
    await ctx.api(`/api/tickets/${encodeURIComponent(ticketId)}/link`, {
      method: "POST",
      body: JSON.stringify({ conversation_id: conversationId }),
    });
    void openTicketDetail(ticketId);
  } catch (error) {
    window.alert(`关联失败：${error.message || error}`);
  }
}

export async function jumpToTicketConversation(conversationId) {
  // 先置 selectedId 再切回队列,并临时抑制 runRefresh 的自动选第一条:
  // 否则 switchWorkspaceTab 内部 silent 前台刷新会在 selectedId 为空/不在
  // 队列时抢开 conversation[0]。await selectConversation 覆盖整个加载窗口。
  ctx.state.selectedId = conversationId;
  suppressAutoSelect++;
  try {
    switchWorkspaceTab("queue");
    await ctx.selectConversation(conversationId);
  } finally {
    suppressAutoSelect--;
  }
}

/** Current auto-select suppression depth (read by app.js runRefresh). */
export function autoSelectSuppressed() {
  return suppressAutoSelect > 0;
}

/**
 * Bind the ticket workspace DOM (exactly once, at app.js load): convert
 * button, workspace tabs, list/detail/transition/link controls.
 */
export function bindTickets() {
  if (!ctx?.els) return false;
  if (ctx.els.ticketBtn) {
    ctx.els.ticketBtn.addEventListener("click", () => void convertToTicket());
  }
  if (ctx.els.wsTabQueue) ctx.els.wsTabQueue.addEventListener("click", () => switchWorkspaceTab("queue"));
  if (ctx.els.wsTabTickets) {
    ctx.els.wsTabTickets.addEventListener("click", () => switchWorkspaceTab("tickets"));
  }
  if (ctx.els.ticketStatusFilter) {
    ctx.els.ticketStatusFilter.addEventListener("change", () => void loadTickets());
  }
  if (ctx.els.ticketList) {
    ctx.els.ticketList.addEventListener("click", (event) => {
      const row = event.target.closest(".ticket-row");
      if (row) void openTicketDetail(row.dataset.ticketId);
    });
  }
  if (ctx.els.ticketBack) {
    ctx.els.ticketBack.addEventListener("click", () => closeTicketDetail());
  }
  if (ctx.els.ticketTransitions) {
    ctx.els.ticketTransitions.addEventListener("click", (event) => {
      const button = event.target.closest(".ticket-transition");
      if (button) void transitionActiveTicket(button.dataset.status);
    });
  }
  if (ctx.els.ticketLinkCurrent) {
    ctx.els.ticketLinkCurrent.addEventListener("click", () => void linkActiveTicketConversation());
  }
  if (ctx.els.ticketDetailConvs) {
    ctx.els.ticketDetailConvs.addEventListener("click", (event) => {
      const row = event.target.closest(".ticket-conv-row");
      if (row) jumpToTicketConversation(row.dataset.convId);
    });
  }
  return true;
}