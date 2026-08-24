/**
 * Helix Support — format / util module (Phase 26.1)
 *
 * Pure formatting and escaping helpers extracted from the monolith. No DOM
 * access; unit-testable with node:test. The legacy app.js keeps its own
 * copies for the running UI; this module is the single source for tests and
 * for the migration target.
 */

/** Escape HTML special characters in an untrusted value. */
export function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

/** Map a conversation status code to a display label. */
export function statusLabel(status, locale = "zh-CN") {
  const labels = {
    "zh-CN": {
      open: "自动处理中",
      waiting_human: "等待人工",
      human_active: "人工处理中",
      resolved: "已解决",
    },
    en: {
      open: "auto",
      waiting_human: "waiting_human",
      human_active: "human_active",
      resolved: "resolved",
    },
  };
  const pack = labels[locale] || labels["zh-CN"];
  return pack[status] || status || "unknown";
}

/** Map a role code to a display label. */
export function roleLabel(role, locale = "zh-CN") {
  const labels = {
    "zh-CN": {
      admin: "管理员",
      supervisor: "主管",
      operator: "客服",
      channel: "渠道",
      viewer: "只读",
      auditor: "审计员",
    },
    en: {
      admin: "admin",
      supervisor: "supervisor",
      operator: "operator",
      channel: "channel",
      viewer: "viewer",
      auditor: "auditor",
    },
  };
  const pack = labels[locale] || labels["zh-CN"];
  return pack[role] || role;
}

/**
 * Format an ISO timestamp for display.
 * @param {string|null} value ISO timestamp
 * @param {boolean} includeDate also show month/day
 * @param {string} [locale] Intl locale, defaults to zh-CN
 * @returns {string}
 */
export function formatTime(value, includeDate = false, locale = "zh-CN") {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(locale, {
    ...(includeDate ? { month: "2-digit", day: "2-digit" } : {}),
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

/** Generate a random idempotency key (16 hex chars, url-safe pattern). */
export function newIdempotencyKey() {
  const bytes = new Uint8Array(8);
  if (typeof crypto !== "undefined" && crypto.getRandomValues) {
    crypto.getRandomValues(bytes);
  } else {
    for (let i = 0; i < bytes.length; i += 1) {
      bytes[i] = Math.floor(Math.random() * 256);
    }
  }
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

/**
 * Compute the next keyset cursor from a paginated response's headers.
 * @param {Headers|object} headers response headers
 * @param {string} headerName cursor header, e.g. "X-Next-Cursor"
 * @returns {string|null}
 */
export function nextCursor(headers, headerName = "X-Next-Cursor") {
  if (!headers) return null;
  const value =
    typeof headers.get === "function" ? headers.get(headerName) : headers[headerName];
  return value || null;
}

/**
 * Exponential backoff with jitter for SSE reconnect scheduling.
 * @param {number} attempt zero-based retry count
 * @param {number} baseMs base delay
 * @param {number} capMs maximum delay
 * @param {function} [random] injectable RNG for deterministic tests
 * @returns {number} delay in ms
 */
export function backoffDelay(attempt, baseMs = 1000, capMs = 30000, random = Math.random) {
  const exp = baseMs * 2 ** Math.min(attempt, 10);
  const jitter = 0.5 + random() * 0.5;
  return Math.min(capMs, Math.round(exp * jitter));
}

export default {
  escapeHtml,
  statusLabel,
  roleLabel,
  formatTime,
  newIdempotencyKey,
  nextCursor,
  backoffDelay,
};
