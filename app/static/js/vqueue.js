/**
 * Helix Support — queue virtualization and diff helpers (ROADMAP §18.4).
 *
 * Pure, DOM-free functions so they run under `node --test` (frontend gate).
 * The legacy app.js owns the DOM: it calls `computeWindow` to decide which
 * rows to render, uses `diffRows` for in-place per-row updates, and only asks
 * the DOM layer for a live row-height measurement via `rowHeightFromElement`.
 */

/** Above this many rows the queue column switches to viewport-window rendering. */
export const VIRTUAL_THRESHOLD = 200;

/**
 * Conservative per-density row estimates (px) used before a live measurement
 * exists; the top edge of these match `.conversation-row` min-heights plus
 * the 8px row margin. `lowPerf` forces the compact profile.
 * @type {{normal: number, compact: number, dense: number}}
 */
export const ROW_HEIGHT_ESTIMATES = Object.freeze({
  normal: 118,
  compact: 80,
  dense: 64,
});

/**
 * Pick the per-density row-height estimate for a density level.
 * @param {"normal"|"compact"|"dense"} [density]
 * @param {boolean} [lowPerf]
 * @returns {number}
 */
export function estimatedRowHeight(density = "normal", lowPerf = false) {
  let level;
  if (lowPerf) level = "compact";
  else if (density === "dense") level = "dense";
  else if (density === "compact") level = "compact";
  else level = "normal";
  return ROW_HEIGHT_ESTIMATES[level] ?? ROW_HEIGHT_ESTIMATES.normal;
}

/**
 * Compute which rows of a fixed-row-height list are inside the scrollport
 * (mirrored with `overscan` rows of slack on each side) plus the pad heights
 * that keep a windowed render aligned to the same scroll positions as a full
 * list.
 *
 * @param {object} opts
 * @param {number} opts.total      total row count
 * @param {number} opts.scrollTop  container scrollTop in px
 * @param {number} opts.viewport   container inner height in px
 * @param {number} opts.rowHeight  measured/enestimated row height in px (>0)
 * @param {number} [opts.overscan] extra rows rendered on each side (>=0)
 * @returns {{first: number, last: number, count: number, topPad: number, bottomPad: number}}
 */
export function computeWindow({ total, scrollTop = 0, viewport = 0, rowHeight = 0, overscan = 4 }) {
  if (total <= 0) return { first: 0, last: -1, count: 0, topPad: 0, bottomPad: 0 };
  const row = Math.max(1, rowHeight);
  const vp = Math.max(0, viewport);
  const over = Math.max(0, overscan);
  const from = Math.floor(Math.max(0, scrollTop) / row) - over;
  const first = Math.max(0, from);
  const last = Math.min(total - 1, Math.ceil((Math.max(0, scrollTop) + vp) / row) + over - 1);
  return {
    first,
    last,
    count: Math.max(0, last - first + 1),
    topPad: first * row,
    bottomPad: Math.max(0, (total - 1 - last) * row),
  };
}

/**
 * Stable per-conversation signature; two rows with the same signature have
 * identical visible content and do not need a DOM update.
 * @param {object} c
 * @returns {string}
 */
export function signatureOf(c) {
  if (!c || !c.id) return "";
  return `${c.id}:${c.status}:${c.updated_at}:${c.version ?? 0}`;
}

/**
 * Diff a previously-rendered signature map against the desired window/list so
 * the DOM layer can update per row (in place) instead of rebuilding a column.
 *
 * `rendered` is a Map of id → signature currently in the DOM; `next` is the
 * desired ordered array of conversations. Returns actions keyed to classify
 * rows for reuse.
 *
 * @param {Map<string,string>} rendered
 * @param {Array<object>} next
 * @param {function} [sig]
 * @returns {{updates: Array<object>, adds: Array<object>, removes: Array<string>, keeps: Array<string>}}
 */
export function diffRows(rendered, next, sig = signatureOf) {
  const updates = [];
  const adds = [];
  const removes = [];
  const keeps = [];
  const prevIds = rendered instanceof Map ? rendered : new Map();
  for (const conversation of next || []) {
    if (!conversation || !conversation.id) continue;
    const oldSignature = prevIds.get(conversation.id);
    if (oldSignature === sig(conversation)) {
      keeps.push(conversation.id);
    } else if (oldSignature !== undefined) {
      updates.push(conversation);
    } else {
      adds.push(conversation);
    }
  }
  for (const id of prevIds.keys()) {
    if (!(next || []).some((c) => c && c.id === id)) removes.push(id);
  }
  return { updates, adds, removes, keeps };
}

/**
 * Read a rendered row's total height including its vertical margin so the
 * windowing math uses live geometry rather than the estimate.
 *
 * The computed-style reader is injected so node tests run DOM-free.
 * @param {HTMLElement} el
 * @param {function} [getComputed]  (el) => CSSStyleDeclaration-like
 * @returns {number|null} full row height in px, or null when unavailable
 */
export function rowHeightFromElement(el, getComputed = null) {
  if (!el) return null;
  const readStyle =
    getComputed ||
    (typeof window !== "undefined" && typeof window.getComputedStyle === "function"
      ? (node) => window.getComputedStyle(node)
      : null);
  if (!readStyle) return null;
  const style = readStyle(el);
  const marginBottom = Number.parseFloat(style.marginBottom || "0") || 0;
  const offsetHeight = typeof el.offsetHeight === "number" ? el.offsetHeight : 0;
  return offsetHeight + marginBottom;
}

export default {
  VIRTUAL_THRESHOLD,
  ROW_HEIGHT_ESTIMATES,
  estimatedRowHeight,
  computeWindow,
  signatureOf,
  diffRows,
  rowHeightFromElement,
};