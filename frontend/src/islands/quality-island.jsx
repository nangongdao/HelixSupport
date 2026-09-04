/**
 * Helix Support — quality dashboard React island (D2)
 *
 * Mounts into #qualityReactIsland and renders the quality trend +
 * intent×version heatmap using the same SVG generation logic as the
 * legacy js/quality-charts.js (framework-agnostic strings injected via
 * dangerouslySetInnerHTML). This is the first island to validate the
 * dual-track architecture. See DESKTOP_TAURI_PLAN.md §3.1 + §D2.
 */

import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  QueryClient,
  QueryClientProvider,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

function escapeXml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function QualityTrend({ buckets }) {
  const [metric, setMetric] = useState("turn_count");
  const width = 560;
  const height = 170;
  const pad = 14;

  const perDay = new Map();
  for (const row of buckets || []) {
    const date = row?.date;
    if (!date) continue;
    const value = Number(row?.[metric]) || 0;
    perDay.set(date, (perDay.get(date) || 0) + value);
  }
  const points = [...perDay.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1));

  if (points.length < 2) {
    return (
      <div className="qc-empty" role="status">
        趋势数据不足，至少需要两天的记录。
      </div>
    );
  }

  const max = Math.max(...points.map(([, v]) => v), 1);
  const plotWidth = width - pad * 2;
  const plotHeight = height - pad * 2;
  const stepX = plotWidth / (points.length - 1);
  const coords = points.map(([date, value], index) => {
    const x = pad + index * stepX;
    const y = pad + plotHeight - (value / max) * plotHeight;
    return { x, y, date, value };
  });
  const line = coords.map((c) => `${c.x.toFixed(1)},${c.y.toFixed(1)}`).join(" ");
  const grid = [0.25, 0.5, 0.75]
    .map((f) => {
      const y = pad + plotHeight - f * plotHeight;
      return `<line class="qc-gridline" x1="${pad}" y1="${y.toFixed(1)}" x2="${width - pad}" y2="${y.toFixed(1)}" />`;
    })
    .join("");
  const dots = coords
    .map(
      (c) =>
        `<circle class="qc-dot" cx="${c.x.toFixed(1)}" cy="${c.y.toFixed(1)}" r="3"><title>${escapeXml(c.date)}: ${c.value}</title></circle>`,
    )
    .join("");
  const labelStep = Math.max(1, Math.ceil(80 / stepX));
  const labels = coords
    .map((c, i) => {
      if (i === points.length - 1 || i % labelStep === 0) {
        return `<text class="qc-axis" x="${c.x.toFixed(1)}" y="${height - 4}">${escapeXml(c.date)}</text>`;
      }
      return "";
    })
    .join("");
  const svg =
    `<svg class="qc-trend" viewBox="0 0 ${width} ${height}" role="img" aria-label="质量趋势（${metric}）">` +
    grid +
    `<polyline class="qc-line" points="${line}" fill="none" />` +
    dots +
    labels +
    `</svg>`;

  return (
    <div className="qc-trend-wrap">
      <label className="qc-metric-select">
        指标
        <select value={metric} onChange={(e) => setMetric(e.target.value)}>
          <option value="turn_count">对话轮次</option>
          <option value="conversation_count">会话数</option>
        </select>
      </label>
      <div dangerouslySetInnerHTML={{ __html: svg }} />
    </div>
  );
}

function QualityHeatmap({ buckets }) {
  const cellH = 22;
  const labelW = 132;
  const labelH = 18;
  const cellW = 64;
  const pad = 8;

  const cells = [];
  for (const row of buckets || []) {
    const intent = row?.intent;
    const version = row?.prompt_version;
    const turns = Number(row?.turn_count) || 0;
    if (!intent || !version) continue;
    cells.push({ intent, version, turns });
  }

  if (!cells.length) {
    return (
      <div className="qc-empty" role="status">
        暂无意图 × 版本数据。
      </div>
    );
  }

  const intents = [...new Set(cells.map((c) => c.intent))].sort();
  const versions = [...new Set(cells.map((c) => c.version))].sort();
  const byKey = new Map();
  for (const cell of cells) {
    const key = `${cell.intent}\u0000${cell.version}`;
    byKey.set(key, (byKey.get(key) || 0) + cell.turns);
  }
  const max = Math.max(...byKey.values(), 1);
  const widthOut = pad + labelW + versions.length * cellW + pad;
  const heightOut = pad + labelH + intents.length * cellH + pad;

  const headers = versions
    .map((version, col) => {
      const x = pad + labelW + col * cellW + cellW / 2;
      return `<text class="qc-axis qc-head" x="${x.toFixed(1)}" y="${pad + labelH - 5}">${escapeXml(version)}</text>`;
    })
    .join("");
  const body = intents
    .map((intent, rowIndex) => {
      const y = pad + labelH + rowIndex * cellH;
      const label = `<text class="qc-axis qc-row" x="${pad + labelW - 5}" y="${(y + cellH / 2 + 4).toFixed(1)}">${escapeXml(intent)}</text>`;
      const rowCells = versions
        .map((version, colIndex) => {
          const turns = byKey.get(`${intent}\u0000${version}`) || 0;
          const x = pad + labelW + colIndex * cellW;
          const title = `${escapeXml(intent)} × ${escapeXml(version)}: ${turns}`;
          if (turns <= 0) {
            return `<rect class="qc-cell qc-cell-empty" x="${x}" y="${y}" width="${cellW}" height="${cellH}"><title>${title}</title></rect>`;
          }
          const ratio = max > 0 ? Math.max(0, Math.min(1, turns / max)) : 0;
          const alpha = (0.12 + ratio * 0.82).toFixed(3);
          const fill = `rgba(45, 212, 191, ${alpha})`;
          return (
            `<rect class="qc-cell" x="${x}" y="${y}" width="${cellW}" height="${cellH}" fill="${fill}"><title>${title}</title></rect>` +
            `<text class="qc-celltext" x="${(x + cellW / 2).toFixed(1)}" y="${(y + cellH / 2 + 4).toFixed(1)}">${turns}</text>`
          );
        })
        .join("");
      return label + rowCells;
    })
    .join("");
  const svg =
    `<svg class="qc-heatmap" viewBox="0 0 ${widthOut} ${heightOut}" role="img" aria-label="意图 × 提示词版本热图">` +
    headers +
    body +
    `</svg>`;

  return <div dangerouslySetInnerHTML={{ __html: svg }} />;
}

export const QUALITY_EVENTS = Object.freeze({
  REFRESH: "helix-quality-refresh",
});

export function QualityIsland() {
  const queryClient = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["quality-buckets"],
    queryFn: async () => {
      const res = await fetch("/api/supervisor/quality", {
        headers: { "X-Tenant-Id": "demo" },
      });
      if (!res.ok) throw new Error(`quality API ${res.status}`);
      return res.json();
    },
    // Mirrors the legacy loadQualityPanel throttle (one fetch per 10s).
    staleTime: 10_000,
  });

  // Legacy drives the refresh cadence: a view re-open dispatches an
  // unforced refresh (only stale data refetches, matching the legacy
  // throttle) and the header 刷新 button dispatches force.
  useEffect(() => {
    const onRefresh = (event) => {
      if (event.detail?.force) {
        void queryClient.invalidateQueries({ queryKey: ["quality-buckets"] });
        return;
      }
      void queryClient.refetchQueries({ queryKey: ["quality-buckets"], stale: true });
    };
    window.addEventListener(QUALITY_EVENTS.REFRESH, onRefresh);
    return () => window.removeEventListener(QUALITY_EVENTS.REFRESH, onRefresh);
  }, [queryClient]);

  const buckets = Array.isArray(data) ? data : data?.buckets || [];

  if (isLoading) {
    return (
      <div className="qc-skeleton" role="status" aria-label="加载中">
        <div className="qc-skeleton-bar" />
        <div className="qc-skeleton-bar" />
      </div>
    );
  }
  if (error) {
    return (
      <div className="qc-error" role="alert">
        质量数据加载失败：{String(error.message || error)}
      </div>
    );
  }

  return (
    <div className="qc-island">
      <QualityTrend buckets={buckets} />
      <QualityHeatmap buckets={buckets} />
    </div>
  );
}

/**
 * Mount the quality island into a host <div>. Called by the island loader.
 * @param {HTMLElement} element - mount point
 */
export function mount(element) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { refetchOnWindowFocus: false } },
  });
  const root = createRoot(element);
  root.render(
    <QueryClientProvider client={queryClient}>
      <QualityIsland />
    </QueryClientProvider>,
  );
}

export default { mount, QualityIsland };
