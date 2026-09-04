/**
 * Helix Support — ticket view React island (D3)
 *
 * Migrates the ticket list rendering to React. The legacy ticket-view.js
 * module is heavily DOM-coupled (uses configure(deps) + ctx.els); this
 * island extracts the list rendering into a self-contained component that
 * fetches from /api/tickets and renders ticket rows with the same DOM
 * structure/class names so visual gates stay green.
 *
 * Mounts into #ticketReactIsland. The detail view stays in the legacy
 * controller during the dual-track period (it has complex state machine
 * interactions with the conversation view).
 *
 * See DESKTOP_TAURI_PLAN.md §D3 (leaf→root: ticket-view after knowledge).
 */

import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";

const TICKET_STATUS_NAMES = {
  open: "待处理",
  in_progress: "处理中",
  closed: "已关闭",
};

function TicketRow({ ticket, onSelect }) {
  const statusName = TICKET_STATUS_NAMES[ticket.status] || ticket.status;
  const isHigh = ticket.priority === "high";
  const ref = ticket.customer_ref ? ` · ${ticket.customer_ref}` : "";
  return (
    <button
      type="button"
      className="ticket-row"
      data-ticket-id={ticket.id}
      onClick={() => onSelect(ticket.id)}
    >
      <span className="ticket-row-main">
        <span className="ticket-subject">{ticket.subject}</span>
        <span className="ticket-meta">
          {ticket.customer_name || "—"}{ref}
        </span>
      </span>
      <span className="ticket-row-side">
        <span className={`priority-pill${isHigh ? " is-high" : ""}`}>
          {isHigh ? "高优" : "普通"}
        </span>
        <span className="status-pill">{statusName}</span>
        <span className="ticket-meta">{ticket.updated_at || ""}</span>
      </span>
    </button>
  );
}

function TicketIsland() {
  const [statusFilter, setStatusFilter] = useState("");

  const { data, isLoading, error } = useQuery({
    queryKey: ["tickets", statusFilter],
    queryFn: async () => {
      const query = statusFilter ? `?status=${encodeURIComponent(statusFilter)}` : "";
      const res = await fetch(`/api/tickets${query}`, {
        headers: { "X-Tenant-Id": "demo" },
      });
      if (!res.ok) throw new Error(`tickets API ${res.status}`);
      return res.json();
    },
    staleTime: 15_000,
  });

  const tickets = Array.isArray(data) ? data : data?.items || [];

  const handleSelect = (ticketId) => {
    // Delegate to the legacy ticket detail opener via custom event.
    window.dispatchEvent(
      new CustomEvent("helix-ticket-open", { detail: { ticketId } }),
    );
  };

  if (isLoading) {
    return (
      <div className="qc-skeleton" role="status" aria-label="工单加载中">
        <div className="qc-skeleton-bar" />
        <div className="qc-skeleton-bar" />
      </div>
    );
  }
  if (error) {
    return (
      <div className="qc-error" role="alert">
        工单加载失败：{String(error.message || error)}
      </div>
    );
  }

  return (
    <div className="ticket-island">
      <div className="ticket-filters">
        <label className="filter-field">
          <span className="sr-only">筛选工单状态</span>
          <select
            aria-label="筛选工单状态"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
          >
            <option value="">全部工单</option>
            <option value="open">待处理</option>
            <option value="in_progress">处理中</option>
            <option value="closed">已关闭</option>
          </select>
        </label>
      </div>
      <div className="ticket-list" aria-live="polite">
        {tickets.length === 0 ? (
          <p className="ticket-empty">暂无工单</p>
        ) : (
          tickets.map((t) => (
            <TicketRow key={t.id} ticket={t} onSelect={handleSelect} />
          ))
        )}
      </div>
    </div>
  );
}

/**
 * Mount the ticket island into a host <div>. Called by the island loader.
 * @param {HTMLElement} element - mount point
 */
export function mount(element) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { refetchOnWindowFocus: false } },
  });
  const root = createRoot(element);
  root.render(
    <QueryClientProvider client={queryClient}>
      <TicketIsland />
    </QueryClientProvider>,
  );
}

export default { mount, TicketIsland };
