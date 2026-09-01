/**
 * Helix Support — conversation queue React island (D3)
 *
 * Renders the conversation queue list AND the footer strip controls in the
 * desktop shell. Data flows through the legacy app.js: it owns polling, SSE,
 * filters, pagination and calls renderQueue(), which in island mode
 * publishes HELIX_QUEUE_UPDATED
 * {conversations, selectedId, canOperate, queueHasMore, queueLoadingMore,
 * compact} instead of painting #conversationList/#queueCount/#loadMore.
 * This island listens, renders the same row classes as queueRowHtml (so
 * visual/axe/perf gates stay valid), and bridges interactions back via the
 * legacy events:
 *   helix-queue-select    {id}       → legacy selectConversation(id)
 *   helix-queue-bulk      {id,on}    → legacy bulk checkbox toggle
 *   helix-queue-load-more {}         → legacy loadMoreConversations()
 * Legacy keeps owning the mentions badge (its own session.js lifecycle), so
 * ui_smoke queue assertions stay green.
 *
 * Mounts into #queueReactIsland (unhidden only when the build is at
 * STATIC_ASSET_VERSION=1.4.0). In a plain browser tab the mount stays hidden
 * and the legacy list renders as before — the island never mounts.
 *
 * This file is the composition root: the row, footer strip, bulk toolbar and
 * the SLA/windowing math live in ./queue/components.jsx (the domain crossed
 * the project's 400-line module limit).
 *
 * See DESKTOP_TAURI_PLAN.md §3.1 + §D3 + §6.2.
 */

import React, { useState, useEffect, useRef, useCallback } from "react";
import { createRoot } from "react-dom/client";

import {
  BulkToolbar,
  ESTIMATED_ROW_HEIGHT,
  QUEUE_EVENTS,
  QueueRow,
  QueueStrip,
  VIRTUAL_THRESHOLD,
  computeWindow,
} from "./queue/components.jsx";

export { QUEUE_EVENTS, parseBulkLabels } from "./queue/components.jsx";

export function QueueIsland() {
  const [snapshot, setSnapshot] = useState(null);
  const [selectedId, setSelectedId] = useState(null);
  const [bulkSelected, setBulkSelected] = useState(() => new Set());
  const [scrollTop, setScrollTop] = useState(0);
  const [bulkBusy, setBulkBusy] = useState(false);
  const listRef = useRef(null);

  // The bulk bridge reports completion so the island's apply button is
  // released whether the action succeeded (selection clears via snapshot)
  // or failed (toast stays legacy).
  useEffect(() => {
    const onApplied = () => setBulkBusy(false);
    window.addEventListener(QUEUE_EVENTS.BULK_APPLIED, onApplied);
    return () => window.removeEventListener(QUEUE_EVENTS.BULK_APPLIED, onApplied);
  }, []);

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
    return (
      <div className="queue-island">
        <div className="queue-loading" role="status" aria-label="队列加载中">正在同步会话队列</div>
        <QueueStrip count={0} hasMore={false} loadingMore={false} />
      </div>
    );
  }

  const queueHasMore = Boolean(snapshot.queueHasMore);
  const queueLoadingMore = Boolean(snapshot.queueLoadingMore);
  if (!conversations.length) {
    return (
      <div className="queue-island">
        <div className="queue-empty">当前筛选条件下没有会话</div>
        <QueueStrip count={0} hasMore={false} loadingMore={false} />
      </div>
    );
  }

  const visibleConversations = win
    ? conversations.slice(win.first, win.last + 1)
    : conversations;

  return (
    <div className="queue-island">
      {canOperate && bulkSelected.size > 0 && (
        <BulkToolbar
          count={bulkSelected.size}
          busy={bulkBusy}
          onApply={({ action, labels }) => {
            setBulkBusy(true);
            window.dispatchEvent(
              new CustomEvent(QUEUE_EVENTS.BULK_APPLY, { detail: { action, labels } }),
            );
          }}
          onClear={() => {
            setBulkSelected(new Set());
            window.dispatchEvent(new CustomEvent(QUEUE_EVENTS.BULK_CLEAR));
          }}
        />
      )}
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
      <QueueStrip
        count={conversations.length}
        hasMore={queueHasMore}
        loadingMore={queueLoadingMore}
      />
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
