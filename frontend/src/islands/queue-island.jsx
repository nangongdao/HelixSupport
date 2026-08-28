/**
 * Helix Support — conversation queue React island (D3)
 *
 * Renders the conversation queue list in the desktop shell. Data flows
 * through the legacy app.js: it owns polling, SSE, filters, pagination and
 * calls renderQueue(), which in island mode publishes HELIX_QUEUE_UPDATED
 * {conversations, selectedId, canOperate, queueHasMore, compact} instead of
 * painting #conversationList. This island listens, renders the same row
 * classes as queueRowHtml (so visual/axe/perf gates stay valid), and bridges
 * row/checkbox interactions back via the legacy events:
 *   helix-queue-select  {id}       → legacy selectConversation(id)
 *   helix-queue-bulk    {id,on}    → legacy bulk checkbox toggle
 * Legacy keeps owning the stripped controls #queueCount/#loadMore and the
 * bulk toolbar (#bulkToolbar), so ui_smoke queue assertions stay green.
 *
 * Mounts into #queueReactIsland (unhidden only when the build is at
 * STATIC_ASSET_VERSION=1.4.0). In a plain browser tab the mount stays hidden
 * and the legacy list renders as before — the island never mounts.
 *
 * See DESKTOP_TAURI_PLAN.md §3.1 + §D3 + §6.2.
 */

import React, { useState, useEffect, useRef, useCallback } from "react";
import { createRoot } from "react-dom/client";

export const QUEUE_EVENTS = Object.freeze({
  UPDATED: "helix-conversations-updated",
  SYNC: "helix-conversations-sync",
  SELECT: "helix-queue-select",
  BULK: "helix-queue-bulk",
});

const VIRTUAL_THRESHOLD = 200;
const ESTIMATED_ROW_HEIGHT = 72;

/* ── status labels (backend enum, mirror legacy statusLabel) ──────────── */

const STATUS_LABELS = {
  open: "自动处理中",
  waiting_human: "等待人工",
  human_active: "人工处理中",
  resolved: "已解决",
};

/* ── SLA text (mirror legacy formatSla) ────────────────────────────────── */

function formatSla(conversation) {
  if (conversation.status === "resolved") return { text: "已完成", breached: false };
  if (!conversation.sla_due_at) return { text: "SLA -", breached: false };
  const milliseconds = new Date(conversation.sla_due_at).getTime() - Date.now();
  const minutes = Math.ceil(Math.abs(milliseconds) / 60000);
  if (milliseconds < 0 || conversation.sla_breached) {
    return { text: `超时 ${minutes} 分钟`, breached: true };
  }
  if (minutes < 60) return { text: `剩余 ${minutes} 分钟`, breached: false };
  return { text: `剩余 ${Math.ceil(minutes / 60)} 小时`, breached: false };
}

/* ── windowing math (from vqueue.js) ───────────────────────────────────── */

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

/* ── row component (mirrors legacy queueRowHtml class names) ─────────── */

function QueueRow({ conversation, active, selected, canOperate, compact, onSelect, onToggleBulk }) {
  const route = conversation.assigned_agent || conversation.intent || "待路由";
  const labels = conversation.labels || [];
  const sla = formatSla(conversation);
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
            onClick={(e) => {
              e.stopPropagation();
              onToggleBulk(conversation.id, e.currentTarget.checked);
            }}
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
          <span className={`item-sla${sla.breached ? " is-breached" : ""}`}>{sla.text}</span>
        </span>
      </button>
    </div>
  );
}

/* ── queue island ──────────────────────────────────────────────────────── */

function QueueIsland() {
  const [snapshot, setSnapshot] = useState(null);
  const [selectedId, setSelectedId] = useState(null);
  const [bulkSelected, setBulkSelected] = useState(() => new Set());
  const [scrollTop, setScrollTop] = useState(0);
  const listRef = useRef(null);

  // The legacy app.js owns the queue data lifecycle (polling, SSE, filters,
  // pagination) and publishes every renderQueue() as a plain DOM event.
  // React state is deliberately NOT the authority — the island is a mirror.
  useEffect(() => {
    const onUpdate = (event) => {
      setSnapshot(event.detail || null);
      setSelectedId(event.detail?.selectedId ?? null);
      setBulkSelected(
        event.detail?.bulkSelected
          ? new Set(event.detail.bulkSelected)
          : new Set(),
      );
    };
    window.addEventListener(QUEUE_EVENTS.UPDATED, onUpdate);
    // The desktop shell mounts islands after the legacy first render, so a
    // snapshot may have already been published without a listener. Ask the
    // legacy side to re-publish the current state once on mount.
    window.dispatchEvent(new CustomEvent(QUEUE_EVENTS.SYNC));
    return () => window.removeEventListener(QUEUE_EVENTS.UPDATED, onUpdate);
  }, []);

  const conversations = snapshot?.conversations || [];
  const canOperate = Boolean(snapshot?.canOperate);
  const compact = Boolean(snapshot?.compact);
  const rowHeight = ESTIMATED_ROW_HEIGHT;
  const useVirtual = conversations.length > VIRTUAL_THRESHOLD;
  const viewport = listRef.current?.clientHeight || 600;
  const win = useVirtual
    ? computeWindow({ total: conversations.length, scrollTop, viewport, rowHeight, overscan: 4 })
    : null;

  const handleScroll = useCallback((e) => {
    setScrollTop(e.currentTarget.scrollTop);
  }, []);

  const handleSelect = useCallback((id) => {
    setSelectedId(id);
    window.dispatchEvent(new CustomEvent(QUEUE_EVENTS.SELECT, { detail: { id } }));
  }, []);

  const handleToggleBulk = useCallback((id, on) => {
    setBulkSelected((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      window.dispatchEvent(new CustomEvent(QUEUE_EVENTS.BULK, { detail: { id, on } }));
      return next;
    });
  }, []);

  if (!snapshot) {
    return <div className="queue-loading" role="status" aria-label="队列加载中">正在同步会话队列</div>;
  }
  if (!conversations.length) {
    return <div className="queue-empty">当前筛选条件下没有会话</div>;
  }

  const visibleConversations = win
    ? conversations.slice(win.first, win.last + 1)
    : conversations;

  return (
    <div className="queue-island">
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
            canOperate={canOperate}
            compact={compact}
            onSelect={handleSelect}
            onToggleBulk={handleToggleBulk}
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
  const root = createRoot(element);
  root.render(<QueueIsland />);
}

export default { mount, QueueIsland };