/**
 * Helix Support — local draft utilities (D3 long tail slice 14).
 *
 * Per-conversation operator draft persistence (localStorage, TTL-gated by
 * the operator's local_drafts_enabled/local_draft_ttl_minutes settings).
 * Extracted from composer.js so the composer module keeps headroom under
 * the 400-line frontend gate while growing the island tool surfaces.
 */

const DRAFT_PREFIX = "helix-draft:";

let ctx = null;

/** Inject the legacy app.js singletons (state only). */
export function configure(deps) {
  ctx = deps;
}

export function draftKey(conversationId) {
  return `${DRAFT_PREFIX}${conversationId}`;
}

export function draftsEnabled() {
  return ctx.state.me?.local_drafts_enabled !== false;
}

export function draftTtlMs() {
  const minutes = Number(ctx.state.me?.local_draft_ttl_minutes) || 720;
  return minutes * 60000;
}

export function loadDraft(conversationId) {
  if (!conversationId || !draftsEnabled()) return "";
  try {
    const raw = window.localStorage.getItem(draftKey(conversationId));
    if (!raw) return "";
    const parsed = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null || typeof parsed.text !== "string") {
      window.localStorage.removeItem(draftKey(conversationId));
      return "";
    }
    if (Date.now() - Number(parsed.savedAt || 0) > draftTtlMs()) {
      window.localStorage.removeItem(draftKey(conversationId));
      return "";
    }
    return parsed.text;
  } catch {
    return "";
  }
}

export function saveDraft(conversationId, value) {
  if (!conversationId || !draftsEnabled()) return;
  try {
    if (!value.trim()) window.localStorage.removeItem(draftKey(conversationId));
    else
      window.localStorage.setItem(
        draftKey(conversationId),
        JSON.stringify({ text: value, savedAt: Date.now() }),
      );
  } catch {
    // localStorage may be unavailable
  }
}

export function clearDraft(conversationId) {
  if (!conversationId) return;
  try {
    window.localStorage.removeItem(draftKey(conversationId));
  } catch {
    // localStorage may be unavailable
  }
}

export function pruneExpiredDrafts() {
  try {
    const ttl = draftTtlMs();
    const stale = [];
    for (let i = 0; i < window.localStorage.length; i += 1) {
      const key = window.localStorage.key(i);
      if (!key || !key.startsWith(DRAFT_PREFIX)) continue;
      if (!draftsEnabled()) {
        stale.push(key);
        continue;
      }
      try {
        const parsed = JSON.parse(window.localStorage.getItem(key) || "");
        if (typeof parsed?.text !== "string" || Date.now() - Number(parsed.savedAt || 0) > ttl) {
          stale.push(key);
        }
      } catch {
        stale.push(key);
      }
    }
    for (const key of stale) window.localStorage.removeItem(key);
  } catch {
    // localStorage may be unavailable
  }
}
