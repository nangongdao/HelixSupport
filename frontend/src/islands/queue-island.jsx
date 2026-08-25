/**
 * Helix Support — queue view React island (D3)
 *
 * Migrates the conversation queue rendering to React, using useReducer with
 * the existing createQueueViewState/reduceQueueView from js/queue-view.js
 * (§43.6 framework-agnostic reducer triplets). The row markup mirrors the
 * legacy queueRowHtml output so visual gates and the 10k render test stay
 * valid. Virtual scrolling (windowed rendering) is preserved via
 * useSyncExternalStore-style scroll tracking — the algorithm from vqueue.js
 * is kept as-is since it is faster and already validated.
 *
 * Mounts into #queueReactIsland. The legacy #conversationList stays during
 * the dual-track period; #queuePane class assertions in ui_smoke are
 * unaffected (the island never touches #queuePane's class attribute).
 *
 * See DESKTOP_TAURI_PLAN.md §3.1 + §D3 + §6.2.
 */

import React, { useReducer, useState, useCallback, useRef, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";

/* ── §43.6 reducer (verbatim from queue-view.js) ────────────────────── */

function createQueueViewState() {
  return {
    virtual: false,
    window: null,
    windowSig: "",
    rowHeight: 0,
    measuring: false,
  };
}

function reduceQueueView(state, action) {
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

/* ── windowing math (from vqueue.js) ──────────────────────────────────── */

function computeWindow({ total, scrollTop, viewport, rowHeight, overscan }) {
  if (total <= 0 || rowHeight <= 0) {
    return { first: 0, last: 0, topPad: 0, bottomPad: 0 };
  }
  const first = Math.max(0, Math.floor(scrollTop / rowHeight) - overscan);
  const visibleCount = Math.ceil(viewport / rowHeight) + 2 * overscan;
  const last = Math.min(total - 1, first + visibleCount);
  const topPad = first * rowHeight;
  const bottomPad = Math.max(0, (total - last - 1) * rowHeight);
  return { first, last, topPad, bottomPad };
}

const VIRTUAL_THRESHOLD = 200;
const ESTIMATED_ROW_HEIGHT = 72;

/* ── status labels ────────────────────────────────────────────────────── */

const STATUS_LABELS = {
  open: "待处理",
  pending: "待响应",
  active: "处理中",
  resolved: "已解决",
  closed: "已关闭",
};

/* ── row component (mirrors legacy queueRowHtml class names) ─────────── */

function QueueRow({ conversation, active, selected, canOperate, compact, onSelect }) {
  const route = conversation.assigned_agent || conversation.intent || "待路由";
  const labels = conversation.labels || [];
  return (
    <div className={`conversation-row${canOperate ? " has-selection" : ""}${selected ? " is-selected" : ""}`}>
      {canOperate && (
        <label className="conversation-select" title={`选择 ${conversation.customer_name}`}>
          <input
            className="conversation-checkbox"
            type="checkbox"
            data-select-id={conversation.id}
            aria-label={`选择 ${conversation.customer_name}`}
            checked={selected}
            readOnly
          />
        </label>
      )}
      <button
        className={`conversation-item${active ? " is-active" : ""}`}
        type="button"
        data-id={conversation.id}
        aria-pressed={active}
        onClick={() => onSelect(conversation.id)}
      >
        <span className="item-top">
          <span className="item-name">{conversation.customer_name}</span>
          <span className={`status-pill ${conversation.status}`}>
            {STATUS_LABELS[conversation.status] || conversation.status}
          </span>
        </span>
        {!compact && (
          <span className="item-preview">{conversation.preview || "尚无消息"}</span>
        )}
        {!compact && (
          <span className="item-labels">
            {labels.length > 0 ? (
              labels.slice(0, 2).map((label) => (
                <span className="label-chip" key={label}>{label}</span>
              ))
            ) : (
              <span className="label-empty">未分类</span>
            )}
            {labels.length > 2 && <span className="label-more">+{labels.length - 2}</span>}
          </span>
        )}
        <span className="item-bottom">
          <span className="item-route">
            {route}
            {conversation.claim_active ? ` · 认领 ${conversation.claimed_by}` : ""}
          </span>
          <span className="item-sla">{conversation.sla_text || ""}</span>
        </span>
      </button>
    </div>
  );
}

/* ── queue island ──────────────────────────────────────────────────────── */

function QueueIsland() {
  const [queueState, dispatch] = useReducer(reduceQueueView, undefined, createQueueViewState);
  const [selectedId, setSelectedId] = useState(null);
  const [bulkSelected, setBulkSelected] = useState(() => new Set());
  const [scrollTop, setScrollTop] = useState(0);
  const listRef = useRef(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["conversations"],
    queryFn: async () => {
      const res = await fetch("/api/conversations", {
        headers: { "X-Tenant-Id": "demo" },
      });
      if (!res.ok) throw new Error(`conversations API ${res.status}`);
      return res.json();
    },
    staleTime: 5_000,
  });

  const conversations = Array.isArray(data) ? data : data?.items || [];
  const rowHeight = ESTIMATED_ROW_HEIGHT;
  const useVirtual = conversations.length > VIRTUAL_THRESHOLD;

  // Dispatch set-conversations when the list changes.
  useEffect(() => {
    dispatch({ type: "set-conversations", virtual: useVirtual, resetWindow: true });
  }, [useVirtual]);

  // Compute the visible window for virtual scrolling.
  const viewport = listRef.current?.clientHeight || 600;
  const win = useVirtual
    ? computeWindow({ total: conversations.length, scrollTop, viewport, rowHeight, overscan: 4 })
    : null;

  const handleScroll = useCallback((e) => {
    setScrollTop(e.currentTarget.scrollTop);
  }, []);

  const handleSelect = useCallback((id) => {
    setSelectedId(id);
    window.dispatchEvent(new CustomEvent("helix-queue-select", { detail: { id } }));
  }, []);

  if (isLoading) {
    return (
      <div className="queue-loading" role="status" aria-label="队列加载中">
        正在同步会话队列
      </div>
    );
  }
  if (error) {
    return (
      <div className="qc-error" role="alert">
        队列加载失败：{String(error.message || error)}
      </div>
    );
  }

  if (!conversations.length) {
    return <div className="queue-empty">当前筛选条件下没有会话</div>;
  }

  const visibleConversations = win
    ? conversations.slice(win.first, win.last + 1)
    : conversations;

  return (
    <div className="queue-island">
      <div className="queue-count">{conversations.length} 个会话</div>
      <div
        ref={listRef}
        className="conversation-list"
        aria-live="polite"
        aria-busy="false"
        onScroll={handleScroll}
      >
        {win && win.topPad > 0 && (
          <div className="vqueue-pad" data-pad="top" aria-hidden="true" style={{ height: `${win.topPad}px` }} />
        )}
        {visibleConversations.map((conv) => (
          <QueueRow
            key={conv.id}
            conversation={conv}
            active={conv.id === selectedId}
            selected={bulkSelected.has(conv.id)}
            canOperate={true}
            compact={false}
            onSelect={handleSelect}
          />
        ))}
        {win && win.bottomPad > 0 && (
          <div className="vqueue-pad" data-pad="bottom" aria-hidden="true" style={{ height: `${win.bottomPad}px` }} />
        )}
      </div>
    </div>
  );
}

/**
 * Mount the queue island into a host <div>. Called by the island loader.
 * @param {HTMLElement} element - mount point
 */
export function mount(element) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { refetchOnWindowFocus: false } },
  });
  const root = createRoot(element);
  root.render(
    <QueryClientProvider client={queryClient}>
      <QueueIsland />
    </QueryClientProvider>,
  );
}

export default { mount, QueueIsland };
