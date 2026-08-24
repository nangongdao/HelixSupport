/**
 * Helix Support — quality dashboard SVG charts (ROADMAP §17.3).
 *
 * The quality dashboard page (and the inspector panel) render two native-SVG
 * charts without any charting library, keeping the zero-build constraint:
 *
 * - ``qualityTrendSvg`` — a line chart of the selected metric (default turn
 *   count) aggregated per day, from the ``/api/supervisor/quality`` buckets.
 * - ``intentVersionHeatmapSvg`` — an intent × prompt-version matrix whose
 *   cells are shaded by relative turn volume.
 *
 * The module is pure and DOM-free so it runs under ``node --test``; app.js
 * injects the bucket rows and splices the returned SVG strings into the
 * dashboard containers. All user-controlled text (intents, versions, dates)
 * is XML-escaped before it can reach the SVG markup.
 */

/** Escape text for safe embedding in SVG/XML markup. */
export function escapeXml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

/**
 * Line chart of a per-day metric, rendered as an SVG polyline.
 *
 * Bucket rows are aggregated by ``date`` (summing the metric) and plotted in
 * chronological order. Returns an empty string when fewer than two data
 * points exist (the caller shows a placeholder instead).
 *
 * @param {Array<object>} buckets quality bucket rows
 * @param {object} [opts]
 * @param {string} [opts.metric="turn_count"] row field to aggregate
 * @param {number} [opts.width] viewBox width
 * @param {number} [opts.height] viewBox height
 * @param {number} [opts.pad] inner padding for the axes
 * @returns {string} SVG markup, or "" when not enough data
 */
export function qualityTrendSvg(buckets, opts = {}) {
  const metric = opts.metric ?? "turn_count";
  const width = opts.width ?? 560;
  const height = opts.height ?? 170;
  const pad = opts.pad ?? 14;

  const perDay = new Map();
  for (const row of buckets || []) {
    const date = row?.date;
    if (!date) continue;
    const value = Number(row?.[metric]) || 0;
    perDay.set(date, (perDay.get(date) || 0) + value);
  }
  const points = [...perDay.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1));
  if (points.length < 2) return "";

  const max = Math.max(...points.map(([, value]) => value), 1);
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
    .map((fraction) => {
      const y = pad + plotHeight - fraction * plotHeight;
      return `<line class="qc-gridline" x1="${pad}" y1="${y.toFixed(1)}" x2="${width - pad}" y2="${y.toFixed(1)}" />`;
    })
    .join("");
  const dots = coords
    .map(
      (c) =>
        `<circle class="qc-dot" cx="${c.x.toFixed(1)}" cy="${c.y.toFixed(1)}" r="3"><title>${escapeXml(c.date)}: ${c.value}</title></circle>`,
    )
    .join("");
  // Date labels every ~80px, always including the newest day.
  const labelStep = Math.max(1, Math.ceil(80 / stepX));
  const labels = coords
    .map((c, index) => {
      if (index === points.length - 1 || index % labelStep === 0) {
        return `<text class="qc-axis" x="${c.x.toFixed(1)}" y="${height - 4}">${escapeXml(c.date)}</text>`;
      }
      return "";
    })
    .join("");
  return (
    `<svg class="qc-trend" viewBox="0 0 ${width} ${height}" role="img" aria-label="质量趋势（${metric}）">` +
    grid +
    `<polyline class="qc-line" points="${line}" fill="none" />` +
    dots +
    labels +
    `</svg>`
  );
}

/**
 * Intent × prompt-version heatmap of relative turn volume.
 *
 * Rows are distinct intents, columns distinct prompt versions; each cell is
 * shaded via ``heatmapColor`` and shows its turn count. Returns "" when the
 * buckets carry no intent/version/turn data.
 *
 * @param {Array<object>} buckets quality bucket rows
 * @param {object} [opts]
 * @param {number} [opts.width] viewBox width
 * @param {number} [opts.height] viewBox height
 * @param {number} [opts.cellH] row height
 * @param {number} [opts.labelW] left label column width
 * @param {number} [opts.labelH] header row height
 * @returns {string} SVG markup, or "" when there is nothing to draw
 */
export function intentVersionHeatmapSvg(buckets, opts = {}) {
  const cellH = opts.cellH ?? 22;
  const labelW = opts.labelW ?? 132;
  const labelH = opts.labelH ?? 18;
  const cellW = opts.cellW ?? 64;
  const pad = opts.pad ?? 8;

  const cells = [];
  for (const row of buckets || []) {
    const intent = row?.intent;
    const version = row?.prompt_version;
    const turns = Number(row?.turn_count) || 0;
    if (!intent || !version) continue;
    cells.push({ intent, version, turns });
  }
  if (!cells.length) return "";

  const intents = [...new Set(cells.map((c) => c.intent))].sort();
  const versions = [...new Set(cells.map((c) => c.version))].sort();
  // Aggregate across dates: the same (intent, version) pair appears once per
  // day in the buckets, so the cell shows the window total, not one day.
  const byKey = new Map();
  for (const cell of cells) {
    const key = `${cell.intent}\u0000${cell.version}`;
    byKey.set(key, (byKey.get(key) || 0) + cell.turns);
  }
  const max = Math.max(...byKey.values(), 1);

  const width = pad + labelW + versions.length * cellW + pad;
  const height = pad + labelH + intents.length * cellH + pad;
  const headers = versions
    .map((version, col) => {
      const x = pad + labelW + col * cellW + cellW / 2;
      return (
        `<text class="qc-axis qc-head" x="${x.toFixed(1)}" y="${pad + labelH - 5}">` +
        escapeXml(version) +
        `</text>`
      );
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
          const fill = heatmapColor(turns, max);
          return (
            `<rect class="qc-cell" x="${x}" y="${y}" width="${cellW}" height="${cellH}" fill="${fill}"><title>${title}</title></rect>` +
            `<text class="qc-celltext" x="${(x + cellW / 2).toFixed(1)}" y="${(y + cellH / 2 + 4).toFixed(1)}">${turns}</text>`
          );
        })
        .join("");
      return label + rowCells;
    })
    .join("");
  const widthOut = pad + labelW + versions.length * cellW + pad;
  const heightOut = pad + labelH + intents.length * cellH + pad;
  return (
    `<svg class="qc-heatmap" viewBox="0 0 ${widthOut} ${heightOut}" role="img" aria-label="意图 × 提示词版本热图">` +
    headers +
    body +
    `</svg>`
  );
}

/** Map a turn volume to a teal-alpha fill; 0 maps to no fill. */
export function heatmapColor(value, max) {
  const ratio = max > 0 ? Math.max(0, Math.min(1, value / max)) : 0;
  if (ratio <= 0) return "";
  const alpha = (0.12 + ratio * 0.82).toFixed(3);
  return `rgba(45, 212, 191, ${alpha})`;
}