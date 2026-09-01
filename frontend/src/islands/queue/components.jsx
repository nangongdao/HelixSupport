/**
 * Helix Support — queue island row, footer strip and bulk toolbar.
 *
 * Split out of queue-island.jsx (400-line module limit). Each component
 * mirrors the legacy DOM contract it replaces — queueRowHtml's class names,
 * #queueCount/#loadMore in the footer, #bulkToolbar's controls — because
 * ui_smoke and the axe passes locate by exactly those.
 */

import React, { useState } from "react";

export const QUEUE_EVENTS = Object.freeze({
  UPDATED: "helix-conversations-updated",
  SYNC: "helix-conversations-sync",
  SELECT: "helix-queue-select",
  BULK: "helix-queue-bulk",
  LOAD_MORE: "helix-queue-load-more",
  BULK_APPLY: "helix-queue-bulk-apply",
  BULK_APPLIED: "helix-queue-bulk-applied",
  BULK_CLEAR: "helix-queue-bulk-clear",
});

export const VIRTUAL_THRESHOLD = 200;
export const ESTIMATED_ROW_HEIGHT = 72;

/* ── status labels (backend enum, mirror legacy statusLabel) ──────────── */

const STATUS_LABELS = {
  open: "自动处理中",
  waiting_human: "等待人工",
  human_active: "人工处理中",
  resolved: "已解决",
};

/* ── SLA text (mirror legacy formatSla) ────────────────────────────────── */

export function formatSla(conversation) {
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

export function computeWindow({ total, scrollTop, viewport, rowHeight, overscan }) {
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

export function QueueRow({ conversation, active, selected, canOperate, compact, onSelect, onToggleBulk }) {
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

/* ── footer strip (mirrors legacy #queueCount/#loadMore) ───────────────── */

export function QueueStrip({ count, hasMore, loadingMore }) {
  return (
    <div className="queue-footer">
      <span>{count}{hasMore ? "+" : ""} 个会话</span>
      <button
        className="queue-more"
        type="button"
        hidden={!hasMore}
        disabled={loadingMore}
        aria-busy={loadingMore}
        onClick={() => window.dispatchEvent(new CustomEvent(QUEUE_EVENTS.LOAD_MORE))}
      >
        <svg className="icon" aria-hidden="true"><use href="/static/icons.svg?v=1.4.0#chevron-down" /></svg>
        <span>加载更多</span>
      </button>
    </div>
  );
}

/* ── bulk toolbar (mirrors legacy #bulkToolbar) ────────────────────────── */

const BULK_ACTIONS = [
  ["priority-high", "设为高优先级"],
  ["priority-normal", "设为普通优先级"],
  ["add-label", "添加标签"],
  ["remove-label", "移除标签"],
  ["claim", "批量认领"],
  ["release", "批量释放"],
];

/** Legacy applyBulkAction label parsing — split on commas (half/full width). */
export function parseBulkLabels(text) {
  return String(text || "")
    .split(/[,，]/)
    .map((label) => label.trim())
    .filter(Boolean);
}

export function BulkToolbar({ count, busy, onApply, onClear }) {
  const [action, setAction] = useState("priority-high");
  const [labelsText, setLabelsText] = useState("");
  const [error, setError] = useState(null);
  const needsLabel = ["add-label", "remove-label"].includes(action);
  const apply = () => {
    const labels = parseBulkLabels(labelsText);
    // Legacy blocks label actions with an empty list (toast + focus);
    // the island surfaces the same copy inline before dispatching.
    if (needsLabel && !labels.length) {
      setError("请输入标签");
      return;
    }
    setError(null);
    onApply({ action, labels });
  };
  return (
    <div className="bulk-toolbar">
      <span className="bulk-count">已选 {count} 项</span>
      <label className="bulk-action-field">
        <span className="sr-only">批量操作</span>
        <select
          aria-label="批量操作"
          value={action}
          onChange={(e) => {
            setAction(e.target.value);
            setError(null);
          }}
        >
          {BULK_ACTIONS.map(([value, label]) => (
            <option value={value} key={value}>{label}</option>
          ))}
        </select>
      </label>
      {needsLabel && (
        <label className="bulk-label-field">
          <span className="sr-only">批量标签</span>
          <input
            type="text"
            maxLength={120}
            placeholder="VIP, 退款风险"
            aria-label="批量标签"
            value={labelsText}
            onChange={(e) => {
              setLabelsText(e.target.value);
              setError(null);
            }}
          />
        </label>
      )}
      {error && <span className="bulk-error" role="alert">{error}</span>}
      <button
        className="bulk-icon-button is-primary"
        type="button"
        title="应用批量操作"
        aria-label="应用批量操作"
        disabled={busy || count === 0}
        onClick={apply}
      >
        <svg className="icon"><use href="/static/icons.svg?v=1.4.0#check" /></svg>
      </button>
      <button
        className="bulk-icon-button"
        type="button"
        title="清除选择"
        aria-label="清除选择"
        onClick={onClear}
      >
        <svg className="icon"><use href="/static/icons.svg?v=1.4.0#x" /></svg>
      </button>
    </div>
  );
}
