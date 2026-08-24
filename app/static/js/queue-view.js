/**
 * Helix Support — queue view module (ROADMAP §43.6 / ARC-001 第三步).
 *
 * The conversation queue column: row markup, full and windowed (virtual)
 * renders, the rAF-throttled scroll window updater, the bulk-selection
 * toolbar, and label chips. Extracted from the legacy app.js; app.js keeps
 * thin delegating wrappers with identical names/signatures, so the running
 * UI behaviour is unchanged. app.js calls configure() once at load time.
 *
 * Windowing math, signatures, and row-height measurement live in vqueue.js
 * (pure, node-tested); this module owns their DOM application.
 *
 * Component contract (the pure part runs under `node --test`):
 * - QUEUE_ROW_PARTS      stable class names a queue row is built from.
 * - createQueueViewState initial render-mode state (full mode, no window).
 * - reduceQueueView      transitions: set-conversations / window-rendered /
 *                        exit-virtual / measure-row. Pure; mirrors the
 *                        legacy `state.queueVirtual`/`queueWindow` fields
 *                        for behavioural parity during migration.
 */

import { computeWindow } from "./vqueue.js?v=1.3.8";

let ctx = null;

/** Inject the legacy app.js singletons (state/els/helpers). */
export function configure(deps) {
  ctx = deps;
}

/** Stable structural parts of one rendered queue row. */
export const QUEUE_ROW_PARTS = Object.freeze({
  ROW: "conversation-row",
  ITEM: "conversation-item",
  SELECT: "conversation-checkbox",
  PAD: "vqueue-pad",
});

/** Initial queue-view state (plain full-column rendering). */
export function createQueueViewState() {
  return {
    virtual: false,
    window: null,
    windowSig: "",
    rowHeight: 0,
    measuring: false,
  };
}

/**
 * Pure mirror of the legacy render-mode transitions. Unknown actions and
 * no-op transitions return the same reference so callers can cheaply detect
 * "nothing changed".
 */
export function reduceQueueView(state, action) {
  switch (action.type) {
    case "set-conversations": {
      if (!action.virtual) {
        if (!state.virtual && state.window === null && !action.resetWindow) return state;
        return { ...state, virtual: false, window: null, windowSig: "" };
      }
      if (state.virtual) return state;
      return { ...state, virtual: true };
    }
    case "window-rendered":
      return { ...state, window: action.window, windowSig: action.windowSig ?? state.windowSig };
    case "exit-virtual": {
      if (!state.virtual && state.window === null) return state;
      return { ...state, virtual: false, window: null, windowSig: "" };
    }
    case "measure-row":
      return { ...state, rowHeight: action.rowHeight, measuring: Boolean(action.measuring) };
    default:
      return state;
  }
}

/** Build one queue row's markup (shared by full and windowed modes). */
export function queueRowHtml(conversation, { showSelection, compactQueue }) {
  const sla = ctx.formatSla(conversation);
  const active = conversation.id === ctx.state.selectedId;
  const route = conversation.assigned_agent || conversation.intent || "待路由";
  const checked = ctx.state.bulkSelected.has(conversation.id);
  const labels = conversation.labels || [];
  const preview = compactQueue
    ? ""
    : `<span class="item-preview">${ctx.escapeHtml(conversation.preview || "尚无消息")}</span>`;
  const labelRow = compactQueue
    ? ""
    : `<span class="item-labels">${renderLabelChips(labels.slice(0, 2), "未分类")}${labels.length > 2 ? `<span class="label-more">+${ctx.escapeHtml(labels.length - 2)}</span>` : ""}</span>`;
  return `
        <div class="conversation-row${showSelection ? " has-selection" : ""}${checked ? " is-selected" : ""}">
          ${showSelection ? `<label class="conversation-select" title="选择 ${ctx.escapeHtml(conversation.customer_name)}">
            <input class="conversation-checkbox" type="checkbox" data-select-id="${ctx.escapeHtml(conversation.id)}" aria-label="选择 ${ctx.escapeHtml(conversation.customer_name)}" ${checked ? "checked" : ""} />
          </label>` : ""}
          <button class="conversation-item${active ? " is-active" : ""}" type="button" data-id="${ctx.escapeHtml(conversation.id)}" aria-pressed="${active}">
          <span class="item-top">
            <span class="item-name">${ctx.escapeHtml(conversation.customer_name)}</span>
            <span class="status-pill ${ctx.escapeHtml(conversation.status)}">${ctx.escapeHtml(ctx.statusLabel(conversation.status))}</span>
          </span>
          ${preview}
          ${labelRow}
          <span class="item-bottom">
            <span class="item-route">${ctx.escapeHtml(route)}${conversation.claim_active ? ` · 认领 ${ctx.escapeHtml(conversation.claimed_by)}` : ""}</span>
            <span class="item-sla${sla.breached ? " is-breached" : ""}">${ctx.escapeHtml(sla.text)}</span>
          </span>
          </button>
        </div>`;
}

/** Label chips shared by queue rows, overview tab, and detail header. */
export function renderLabelChips(labels, emptyText = "无标签") {
  if (!labels?.length) return `<span class="label-empty">${ctx.escapeHtml(emptyText)}</span>`;
  return labels.map((label) => `<span class="label-chip">${ctx.escapeHtml(label)}</span>`).join("");
}

export function renderBulkToolbar() {
  const els = ctx.els;
  const count = ctx.state.bulkSelected.size;
  els.bulkToolbar.hidden = !ctx.canOperate() || count === 0;
  els.bulkCount.textContent = `已选 ${count} 项`;
  const needsLabel = ["add-label", "remove-label"].includes(els.bulkAction.value);
  els.bulkLabelField.hidden = !needsLabel;
  els.applyBulk.disabled = count === 0;
}

function renderFullQueue(opts) {
  const { state, els } = ctx;
  const parts = new Array(state.conversations.length);
  for (let index = 0; index < state.conversations.length; index += 1) {
    parts[index] = queueRowHtml(state.conversations[index], opts);
  }
  els.list.innerHTML = parts.join("");
}

export function windowedRowHeight() {
  return ctx.state.queueRowHeight || ctx.estimatedRowHeight(ctx.state.density, ctx.state.lowPerf);
}

function currentQueueWindow(rowHeight) {
  return computeWindow({
    total: ctx.state.conversations.length,
    scrollTop: ctx.els.list.scrollTop,
    viewport: ctx.els.list.clientHeight,
    rowHeight,
    overscan: 4,
  });
}

export function renderWindowedQueue(win, rowHeight, opts) {
  const { state, els } = ctx;
  const parts = [];
  // CSP style-src 'self' blocks inline style attributes, so pad heights are
  // applied through the CSSOM after the markup is in place.
  if (win.topPad > 0) {
    parts.push('<div class="vqueue-pad" data-pad="top" aria-hidden="true"></div>');
  }
  for (let index = win.first; index <= win.last; index += 1) {
    parts.push(queueRowHtml(state.conversations[index], opts));
  }
  if (win.bottomPad > 0) {
    parts.push('<div class="vqueue-pad" data-pad="bottom" aria-hidden="true"></div>');
  }
  els.list.innerHTML = parts.join("");
  const topPad = els.list.querySelector('.vqueue-pad[data-pad="top"]');
  const bottomPad = els.list.querySelector('.vqueue-pad[data-pad="bottom"]');
  if (topPad) topPad.style.height = `${win.topPad}px`;
  if (bottomPad) bottomPad.style.height = `${win.bottomPad}px`;
  state.queueWindow = win;
  // Measure the first live row exactly once, then align with a corrective
  // re-render so the pads match real geometry instead of the estimate.
  if (!state.queueRowHeight && !state.queueMeasuring) {
    const firstRow = els.list.querySelector(".conversation-row");
    const measured = firstRow ? ctx.rowHeightFromElement(firstRow) : null;
    if (measured && measured !== rowHeight) {
      state.queueMeasuring = true;
      state.queueRowHeight = measured;
      renderQueue();
      state.queueMeasuring = false;
      return;
    }
    state.queueRowHeight = measured || rowHeight;
  }
}

export function renderQueue() {
  const { state, els } = ctx;
  els.queueCount.textContent = `${state.conversations.length}${state.queueHasMore ? "+" : ""} 个会话`;
  els.loadMore.hidden = !state.queueHasMore;
  els.loadMore.disabled = state.queueLoadingMore;
  els.loadMore.setAttribute("aria-busy", String(state.queueLoadingMore));
  els.list.setAttribute("aria-busy", "false");
  if (!state.conversations.length) {
    els.loadMore.hidden = true;
    els.list.innerHTML = '<div class="queue-empty">当前筛选条件下没有会话</div>';
    return;
  }
  const opts = {
    showSelection: ctx.canOperate(),
    compactQueue: ctx.isCompactDensity(state.density, state.lowPerf),
  };
  if (state.conversations.length > ctx.VIRTUAL_THRESHOLD) {
    state.queueVirtual = true;
    const rowHeight = windowedRowHeight();
    renderWindowedQueue(currentQueueWindow(rowHeight), rowHeight, opts);
  } else {
    if (state.queueVirtual) {
      state.queueVirtual = false;
      state.queueWindow = null;
      state.queueWindowSig = "";
    }
    renderFullQueue(opts);
  }
  renderBulkToolbar();
}

export function renderLoadingQueue() {
  const els = ctx.els;
  els.list.setAttribute("aria-busy", "true");
  els.list.innerHTML = '<div class="queue-loading">正在同步会话队列</div>';
}

// ROADMAP §18.4: refresh only when the visible band or its content signature
// changed, throttled to one update per animation frame.
export function scheduleQueueWindowUpdate() {
  const { state, els } = ctx;
  if (!state.queueVirtual) return;
  if (state.queueScrollRaf) return;
  state.queueScrollRaf = window.requestAnimationFrame(() => {
    state.queueScrollRaf = 0;
    if (!state.queueVirtual || !state.conversations.length) return;
    const rowHeight = windowedRowHeight();
    const win = currentQueueWindow(rowHeight);
    const sig = [];
    for (let index = win.first; index <= win.last; index += 1) {
      sig.push(ctx.signatureOf(state.conversations[index]));
    }
    const windowSig = sig.join("|");
    if (
      state.queueWindow &&
      state.queueWindow.first === win.first &&
      state.queueWindow.last === win.last &&
      state.queueWindowSig === windowSig
    ) {
      return; // band and content unchanged — nothing to repaint
    }
    state.queueWindowSig = windowSig;
    // Suppress the polite-live announcement while the window churns on scroll.
    const previousLive = els.list.hasAttribute("aria-live")
      ? els.list.getAttribute("aria-live")
      : null;
    els.list.setAttribute("aria-live", "off");
    renderWindowedQueue(win, rowHeight, {
      showSelection: ctx.canOperate(),
      compactQueue: ctx.isCompactDensity(state.density, state.lowPerf),
    });
    if (previousLive) els.list.setAttribute("aria-live", previousLive);
    else els.list.removeAttribute("aria-live");
  });
}

export function handleQueueScroll() {
  scheduleQueueWindowUpdate();
}
