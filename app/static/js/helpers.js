/**
 * Helix Support — shared UI/format helpers (app.js <500 campaign).
 *
 * The console's cross-cutting helpers, extracted verbatim from app.js:
 * escapeHtml/icon/formatTime/formatSla/newIdempotencyKey (pure),
 * statusLabel/roleLabel (i18n-aware with hardcoded fallbacks), and the
 * DOM-touching showToast (configure-injected els)/scheduleIdle/setFormBusy.
 *
 * js/format.js is the separate Phase 26.1 test module; its statusLabel/
 * roleLabel ignore the i18n pack and its newIdempotencyKey lacks the ui-
 * prefix, so the running console must keep these exact copies (same reason
 * js/http.js exists alongside js/api.js).
 */

let els = null;
let toastTimer = null;

/** Inject the legacy app.js DOM elements (the toast pipe) — boot, once. */
export function configure(deps) {
  els = deps.els;
}

/** Escape HTML special characters in an untrusted value. */
export function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

/** Build the inline SVG icon use-tag for a sprite id. */
export function icon(name) {
  return `<svg class="icon" aria-hidden="true"><use href="/static/icons.svg?v=1.4.0#${name}"></use></svg>`;
}

/** Map a conversation status code to a display label (i18n-aware). */
export function statusLabel(status) {
  // Phase 26.3: prefer the i18n pack when the module layer has loaded.
  const i18n = window.HelixModules?.i18n;
  if (i18n) {
    const translated = i18n.t(`status.${status}`);
    return translated || status || "未知";
  }
  return (
    {
      open: "自动处理中",
      waiting_human: "等待人工",
      human_active: "人工处理中",
      resolved: "已解决",
    }[status] || status || "未知"
  );
}

/** Map a role code to a display label (i18n-aware). */
export function roleLabel(role) {
  const i18n = window.HelixModules?.i18n;
  if (i18n) return i18n.t(`role.${role}`);
  return (
    {
      admin: "管理员",
      supervisor: "主管",
      operator: "客服",
      channel: "渠道",
      viewer: "只读",
      auditor: "审计员",
    }[role] || role
  );
}

/** Format an ISO timestamp for display (zh-CN, optional date prefix). */
export function formatTime(value, includeDate = false) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    ...(includeDate ? { month: "2-digit", day: "2-digit" } : {}),
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

/** Summarise SLA breach/remaining time for a conversation row. */
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

/** Show a transient toast; isError styles it and it auto-hides after 3.6s. */
export function showToast(message, isError = false) {
  window.clearTimeout(toastTimer);
  els.toast.textContent = message;
  els.toast.classList.toggle("is-error", isError);
  els.toast.hidden = false;
  toastTimer = window.setTimeout(() => {
    els.toast.hidden = true;
  }, 3600);
}

// ROADMAP §18.4 渲染预算: run non-critical background work when the browser
// is idle so operator interactions stay under budget; a short timeout is the
// fallback where requestIdleCallback is unavailable. Deferred work must be
// self-contained (the wrapped functions already guard their own state).
export function scheduleIdle(fn, timeoutMs = 2000) {
  const run = () => {
    try {
      fn();
    } catch (error) {
      console.error("idle task failed", error);
    }
  };
  if (typeof window.requestIdleCallback === "function") {
    window.requestIdleCallback(run, { timeout: timeoutMs });
  } else {
    window.setTimeout(run, 250);
  }
}

/** Toggle the busy frame on a form (disables every control inside it). */
export function setFormBusy(form, busy) {
  form.dataset.busy = String(busy);
  form.setAttribute("aria-busy", String(busy));
  form.querySelectorAll("button, input, textarea, select").forEach((control) => {
    control.disabled = busy;
  });
}

/** Generate an idempotency key sharing the console's ui- prefix. */
export function newIdempotencyKey() {
  if (window.crypto?.randomUUID) return `ui-${window.crypto.randomUUID()}`;
  return `ui-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export default {
  configure,
  escapeHtml,
  icon,
  statusLabel,
  roleLabel,
  formatTime,
  formatSla,
  showToast,
  scheduleIdle,
  setFormBusy,
  newIdempotencyKey,
};