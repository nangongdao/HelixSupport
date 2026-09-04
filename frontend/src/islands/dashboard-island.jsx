/**
 * Helix Support — dashboard island (D3 long tail: dashboard metrics).
 *
 * Owns the workspace metrics strip (#metrics) in the desktop shell: the
 * four-tile readout (自动/待响应/认领中/SLA 超时) fed by /api/dashboard.
 * Mounts into #dashboardReactIsland; the legacy #metrics grid is yielded
 * (hidden) while the mount exists.
 *
 * Refresh semantics mirror legacy exactly: legacy refetches the dashboard
 * only on foreground refreshAll cycles (initial load, user actions, search
 * — background 30s polls reuse the cached readout), so app.js dispatches
 * helix-dashboard-refresh {force:true} on those same foreground cycles in
 * island mode and the island refetches; background cycles dispatch nothing.
 *
 * metricsModel captures the net effect of legacy renderMetrics — the four
 * items are built with a 待人工 tile that is immediately relabeled 待响应
 * (needs_response) before paint, so the rendered set never shows
 * waiting_human.
 *
 * See DESKTOP_TAURI_PLAN.md §D3 (app.js long tail).
 */

import React, { useCallback, useEffect } from "react";
import { createRoot } from "react-dom/client";
import {
  QueryClient,
  QueryClientProvider,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

export const DASHBOARD_EVENTS = Object.freeze({
  REFRESH: "helix-dashboard-refresh",
});

/** Legacy api() contract: tenant header from the host document. */
async function fetchDashboard() {
  const tenant = document.documentElement.dataset.tenantId || "demo";
  const res = await fetch("/api/dashboard", { headers: { "X-Tenant-Id": tenant } });
  if (!res.ok) throw new Error(`dashboard API ${res.status}`);
  return res.json();
}

/**
 * Net rendering of legacy renderMetrics: the 待人工 tile is constructed
 * first but overwritten in place by 待响应/needs_response, so the visible
 * strip is open / needs_response / claimed_active / sla_breached with
 * alert styling on the latter two when > 0.
 * @param {Object|undefined} data - /api/dashboard payload
 * @returns {[string, number, boolean][]} [label, value, alert] rows
 */
export function metricsModel(data) {
  return [
    ["自动", data?.open ?? 0, false],
    ["待响应", data?.needs_response ?? 0, (data?.needs_response ?? 0) > 0],
    ["认领中", data?.claimed_active ?? 0, false],
    ["SLA 超时", data?.sla_breached ?? 0, (data?.sla_breached ?? 0) > 0],
  ];
}

export function DashboardIsland() {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["dashboard"],
    queryFn: fetchDashboard,
    // Legacy has no staleness window — foreground cycles always refetch.
    // The island only refetches when legacy dispatches a foreground event.
    staleTime: Infinity,
  });

  const onRefresh = useCallback(
    (event) => {
      if (event.detail?.force) {
        void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      }
    },
    [queryClient],
  );

  useEffect(() => {
    window.addEventListener(DASHBOARD_EVENTS.REFRESH, onRefresh);
    return () => window.removeEventListener(DASHBOARD_EVENTS.REFRESH, onRefresh);
  }, [onRefresh]);

  // Legacy paints nothing into #metrics until the first refresh lands —
  // mirror that instead of flashing zeros.
  if (query.data === undefined) {
    return <div className="metric-grid" aria-live="polite" data-loading="true" />;
  }
  return (
    <div className="metric-grid" aria-live="polite">
      {metricsModel(query.data).map(([label, value, alert]) => (
        <div className={`metric${alert ? " is-alert" : ""}`} key={label}>
          <span>{label}</span>
          <strong>{value}</strong>
        </div>
      ))}
    </div>
  );
}

/**
 * Mount the dashboard island into a host <div>. Called by the island loader.
 * @param {HTMLElement} element - mount point
 */
export function mount(element) {
  const queryClient = new QueryClient();
  const root = createRoot(element);
  root.render(
    <QueryClientProvider client={queryClient}>
      <DashboardIsland />
    </QueryClientProvider>,
  );
}

export default { mount, DashboardIsland };
