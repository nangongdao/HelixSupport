/**
 * Helix Support — refresh & queue stream lifecycle (app.js <500 slice 23).
 *
 * The foreground/background refresh cycle (refreshAll dedup + runRefresh
 * fan-out), the SSE queue stream connector with reconnect, the BroadcastChannel
 * leader relay (ROADMAP §18.4: only the leader holds /api/events/queue;
 * followers refresh through relayed events), the polling fallback and the
 * visibilitychange/unhandledrejection lifecycle. Extracted from the legacy
 * app.js verbatim with ctx injection; app.js keeps thin wrappers
 * (refreshAll/setLiveStatus/schedulePolling) for its internal callers.
 *
 * App.js-scoped render/derive helpers (renderQueue, conversationQuery,
 * queueSignature, loadLabelCatalog, renderMetrics, loadDetail,
 * selectConversation, …) arrive through configure under `actions` — they read
 * the same state/els singletons this module mutates.
 */

let ctx = null;

/** Inject the legacy app.js singletons (state/els/api/helpers). */
export function configure(deps) {
  ctx = deps;
}

export function relayClientId() {
  if (window.crypto?.randomUUID) return `tab-${window.crypto.randomUUID()}`;
  return `tab-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

export function setLiveStatus(mode) {
  if (!ctx.els.liveStatus) return;
  ctx.els.liveStatus.textContent = mode === "live" ? "LIVE" : mode === "poll" ? "POLL" : "…";
  ctx.els.liveStatus.classList.toggle("is-poll", mode === "poll");
  ctx.els.liveStatus.classList.toggle("is-connecting", mode === "connecting");
}

export function connectQueueEvents() {
  if (ctx.state.queueEventSource?.abort) {
    ctx.state.queueEventSource.abort();
    ctx.state.queueEventSource = null;
  }
  if (ctx.state.queueReconnectTimer) {
    window.clearTimeout(ctx.state.queueReconnectTimer);
    ctx.state.queueReconnectTimer = null;
  }
  if (ctx.state.lowPerf || document.hidden) {
    setLiveStatus("poll");
    return;
  }
  setLiveStatus("connecting");
  const controller = new AbortController();
  ctx.state.queueEventSource = controller;
  void (async () => {
    try {
      const response = await fetch("/api/events/queue?timeout=45", {
        headers: ctx.BASE_HEADERS,
        signal: controller.signal,
      });
      if (!response.ok || !response.body) {
        throw new Error(`queue stream failed (${response.status})`);
      }
      setLiveStatus("live");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const chunks = buffer.split("\n\n");
        buffer = chunks.pop() || "";
        for (const chunk of chunks) {
          const lines = chunk.split("\n");
          let eventName = "message";
          for (const line of lines) {
            if (line.startsWith("event:")) eventName = line.slice(6).trim();
          }
          if (eventName === "queue") {
            if (!document.hidden) {
              void refreshAll({ silent: true, background: true, refreshDetail: false });
              // Single shared connection: the leader tells every follower to
              // refresh too (ROADMAP §18.4).
              if (ctx.state.queueRelay?.isLeader()) {
                ctx.state.queueRelay.relay({ type: "queue" });
              }
            }
          } else if (eventName === "timeout") {
            break;
          }
        }
      }
    } catch (error) {
      if (error.name === "AbortError") return;
      setLiveStatus("poll");
    } finally {
      if (ctx.state.queueEventSource === controller) ctx.state.queueEventSource = null;
      if (!document.hidden && !ctx.state.lowPerf) {
        ctx.state.queueReconnectTimer = window.setTimeout(() => connectQueueEvents(), 4000);
      }
    }
  })();
}

export function initQueueRelay() {
  const broadcast = window.HelixModules?.broadcast;
  if (ctx.state.lowPerf || !broadcast?.createLeaderElector || !window.BroadcastChannel) {
    // No shared connection available — stand down any existing relay and
    // fall back to each tab opening its own stream (legacy behaviour).
    if (ctx.state.queueRelay?.isRunning()) {
      ctx.state.queueRelay.abandon("no-relay");
    }
    ctx.state.queueRelay = null;
    connectQueueEvents();
    return;
  }
  if (ctx.state.queueRelay?.isRunning()) return;
  const { PROTOCOL, createLeaderElector } = broadcast;
  const channel = new BroadcastChannel(PROTOCOL.channelName);
  const elector = createLeaderElector({
    channel: {
      postMessage: (message) => channel.postMessage(message),
      addListener: (fn) => {
        const handler = (event) => fn(event.data);
        channel.addEventListener("message", handler);
        return () => channel.removeEventListener("message", handler);
      },
    },
    clientId: relayClientId(),
    now: () => Date.now(),
    setTimer: (fn, ms) => window.setTimeout(fn, ms),
    clearTimer: (handle) => window.clearTimeout(handle),
    onBecomeLeader: () => connectQueueEvents(),
    onSteppedDown: () => {
      if (ctx.state.queueEventSource?.abort) ctx.state.queueEventSource.abort();
      setLiveStatus("poll");
    },
    onEvent: () => {
      if (!document.hidden) {
        void refreshAll({ silent: true, background: true, refreshDetail: false });
      }
    },
  });
  elector.start();
  ctx.state.queueRelay = elector;
}

export function schedulePolling() {
  if (ctx.state.pollTimer) window.clearInterval(ctx.state.pollTimer);
  ctx.state.pollTimer = window.setInterval(() => {
    if (!document.hidden) {
      void refreshAll({ silent: true, background: true, refreshDetail: false });
    }
  }, ctx.actions.pollInterval());
  initQueueRelay();
}

export async function refreshAll({ silent = false, refreshDetail = true, background = false } = {}) {
  if (ctx.state.refreshPromise) return ctx.state.refreshPromise;
  const refreshTask = runRefresh({ silent, refreshDetail, background });
  ctx.state.refreshPromise = refreshTask;
  try {
    return await refreshTask;
  } finally {
    if (ctx.state.refreshPromise === refreshTask) ctx.state.refreshPromise = null;
  }
}

/** Paint the four workspace metric tiles; the 待人工 tile re-labels to
 * 待响应 with the needs_response count (ROADMAP §17.1 readout parity). */
export function renderMetrics(data) {
  const items = [
    ["自动", data?.open ?? 0, false],
    ["待人工", data?.waiting_human ?? 0, (data?.waiting_human ?? 0) > 0],
    ["认领中", data?.claimed_active ?? 0, false],
    ["SLA 超时", data?.sla_breached ?? 0, (data?.sla_breached ?? 0) > 0],
  ];
  ctx.els.metrics.innerHTML = items
    .map(
      ([label, value, alert]) =>
        `<div class="metric${alert ? " is-alert" : ""}"><span>${ctx.escapeHtml(label)}</span><strong>${ctx.escapeHtml(value)}</strong></div>`,
    )
    .join("");
  const responseMetric = ctx.els.metrics.children[1];
  if (responseMetric) {
    responseMetric.querySelector("span").textContent = "待响应";
    responseMetric.querySelector("strong").textContent = String(data?.needs_response ?? 0);
    responseMetric.classList.toggle("is-alert", (data?.needs_response ?? 0) > 0);
  }
}

export async function runRefresh({ silent = false, refreshDetail = true, background = false } = {}) {
  const { state, els, actions } = ctx;
  if (!silent) {
    els.refreshList.classList.add("is-spinning");
    if (!state.conversations.length) actions.renderLoadingQueue();
  }
  try {
    const meRequest = state.me ? Promise.resolve(state.me) : ctx.api("/api/me");
    const queueOnly = background;
    const staleDashboard = !state.dashboard || !background;
    // Island mode: the dashboard island owns /api/dashboard. Legacy only
    // refetched on foreground cycles (background polls reuse the cached
    // readout), so hand the refresh over on exactly those cycles and never
    // touch the yielded #metrics strip.
    const islandDashboard = window.__HELIX_ISLAND_MODE__;
    if (islandDashboard && !background) {
      window.dispatchEvent(new CustomEvent("helix-dashboard-refresh", { detail: { force: true } }));
    }
    const requests = [
      meRequest,
      staleDashboard && !islandDashboard
        ? ctx.api("/api/dashboard")
        : Promise.resolve(state.dashboard),
      ctx.apiWithHeaders(`/api/conversations?${actions.conversationQuery()}`),
      queueOnly
        ? Promise.resolve(state.labelCatalog)
        : actions.loadLabelCatalog({ force: !state.labelsLoadedAt }),
    ];
    const [me, dashboard, conversationPage, labelCatalog] = await Promise.all(requests);
    const conversations = conversationPage.data;
    const signature = actions.queueSignature(conversations);
    const queueChanged = signature !== state.lastQueueSignature;
    const firstMe = !state.me;
    state.me = me;
    // Expose the operator identity so the desktop React islands (knowledge
    // canWrite gate, admin admin:manage gate, terminal §4.1 RBAC) can read
    // it without re-fetching /api/me. The helix-identity event closes the
    // mount race: islands that mount before /api/me returns keep every
    // privileged query disabled until this dispatch lands. In a browser
    // tab the islands never mount and all of this is inert.
    if (typeof window !== "undefined") {
      window.__HELIX_ROLE__ = me?.role || "guest";
      window.__HELIX_PERMISSIONS__ = me?.permissions || [];
      window.__HELIX_ACTOR__ = me?.actor_id || "";
      window.dispatchEvent(new CustomEvent("helix-identity", {
        detail: {
          role: window.__HELIX_ROLE__,
          permissions: window.__HELIX_PERMISSIONS__,
          actorId: window.__HELIX_ACTOR__,
          tenantId: me?.tenant_id || ctx.TENANT,
        },
      }));
    }
    if (firstMe) actions.pruneExpiredDrafts();
    if (firstMe) ctx.actions.scheduleIdle(() => actions.loadMentions());
    // roster 节流刷新:首次失败下个周期重试,成功后在后台周期更新,
    // 新邀请的同事最多 ~2 分钟出现在 @ 候选(HIGH-2 修复)。
    if (firstMe || !state.collaboratorsLoadedAt || Date.now() - state.collaboratorsLoadedAt > 120000) {
      ctx.actions.scheduleIdle(() => actions.loadCollaborators());
    }
    if (dashboard) state.dashboard = dashboard;
    if (labelCatalog) state.labelCatalog = labelCatalog;
    state.conversations = conversations;
    state.lastQueueSignature = signature;
    if (!queueOnly) await actions.loadCannedResponses({ force: !state.cannedLoadedAt });
    const visibleIds = new Set(conversations.map((conversation) => conversation.id));
    state.bulkSelected = new Set(
      [...state.bulkSelected].filter((conversationId) => visibleIds.has(conversationId)),
    );
    state.queueHasMore = conversationPage.response.headers.get("X-Has-More") === "true";
    state.queueCursor = conversationPage.response.headers.get("X-Next-Cursor");
    // Island mode: the identity island owns the readout (yielded span) and
    // subscribes to the helix-identity dispatch above — skip painting the
    // hidden legacy span.
    if (!window.__HELIX_ISLAND_MODE__) {
      els.operatorIdentity.textContent = `${me.actor_id} · ${actions.roleLabel(me.role)}`;
    }
    if (!queueOnly) actions.renderLabelFilter();
    els.focusWaiting.setAttribute("aria-pressed", String(els.ownershipFilter.value === "needs_response"));
    if (state.dashboard && (!background || staleDashboard)) renderMetrics(state.dashboard);
    if (!background || queueChanged || !els.list.children.length) actions.renderQueue();

    const selected = state.selectedId
      ? conversations.find((conversation) => conversation.id === state.selectedId)
      : null;
    if (selected) {
      const detailStale =
        !state.detail ||
        state.detail.conversation?.id !== selected.id ||
        state.detail.conversation?.version !== selected.version ||
        state.detail.conversation?.updated_at !== selected.updated_at;
      if (refreshDetail && !background && detailStale) await actions.loadDetail(state.selectedId);
      else if (refreshDetail && background && detailStale) void actions.loadDetail(state.selectedId);
    } else if (conversations.length) {
      if (!background && !window.HelixModules?.ticketView?.autoSelectSuppressed?.()) await actions.selectConversation(conversations[0].id);
    } else if (!background) {
      actions.clearSelection();
    }
  } catch (error) {
    if (!background) ctx.showToast(error.message, true);
    if (!state.conversations.length) {
      els.list.innerHTML = `<div class="queue-empty">${ctx.escapeHtml(error.message)}</div>`;
    }
  } finally {
    els.refreshList.classList.remove("is-spinning");
  }
}

/** Bind the tab lifecycle (exactly once, at boot): abort the stream and step
 * down from leadership when hidden, resume + silent-refresh when visible. */
export function bindRefresh() {
  if (typeof window === "undefined") return false;
  window.addEventListener("unhandledrejection", (event) => {
    ctx.showToast(event.reason?.message || "操作失败", true);
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      // Abort our own stream and release group leadership so a visible tab
      // (which still needs queue updates) can take over immediately instead of
      // waiting out our lease.
      if (ctx.state.queueEventSource?.abort) ctx.state.queueEventSource.abort();
      if (ctx.state.queueRelay?.isRunning()) {
        ctx.state.queueRelay.abandon("hidden"); // also fires onSteppedDown -> clean-up
        ctx.state.queueEventSource = null;
      }
      return;
    }
    if (ctx.state.queueRelay) {
      ctx.state.queueRelay.start(); // no-op when still running; re-joins after abandon
    } else {
      connectQueueEvents();
    }
    void refreshAll({ silent: true, background: true, refreshDetail: false });
  });
  return true;
}

export default {
  relayClientId, setLiveStatus, connectQueueEvents, initQueueRelay,
  schedulePolling, refreshAll, runRefresh, renderMetrics, bindRefresh,
};
