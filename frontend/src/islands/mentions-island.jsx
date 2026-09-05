/**
 * Helix Support — mentions island (D3 long tail).
 *
 * Owns the mentions inbox in the desktop shell: the footer badge (rendered
 * through a portal into the queue footer, next to the legacy live dot) and
 * the mentions panel drawer. Legacy session.js keeps the write lifecycle —
 * markMentionRead (POST + toast) and selectConversation (jump) — via the
 * bridges:
 *   helix-mentions-open-jump  {conversationId} → legacy selectConversation
 *   helix-mentions-mark-read  {id}             → legacy markMentionRead
 *   helix-mentions-changed    {}               ← legacy after a read lands
 *
 * The island fetches /api/mentions itself (legacy loadMentions skips in
 * island mode) and mirrors legacy semantics: fetch on mount/open/read, the
 * badge hides when unread is 0 and the panel is closed, and the whole
 * inbox requires conversation:read (gated through the helix-identity
 * broadcast).
 *
 * Element ids get a React suffix; the yielded legacy badge/panel keep the
 * originals for the browser dual-track.
 *
 * See DESKTOP_TAURI_PLAN.md §D3 (app.js long tail).
 */

import React, { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from "@tanstack/react-query";

export const MENTION_EVENTS = Object.freeze({
  IDENTITY: "helix-identity",
  OPEN_JUMP: "helix-mentions-open-jump",
  MARK_READ: "helix-mentions-mark-read",
  CHANGED: "helix-mentions-changed",
});

/** React-suffixed ids: the yielded legacy badge/panel keep the originals. */
export const MENTION_IDS = Object.freeze({
  badge: "mentionsBadgeReact",
  count: "mentionsCountReact",
});

function formatTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

async function fetchMentions() {
  const tenant = document.documentElement.dataset.tenantId || "demo";
  const res = await fetch("/api/mentions", { headers: { "X-Tenant-Id": tenant } });
  if (!res.ok) throw new Error(`mentions API ${res.status}`);
  return res.json();
}

/** Globals snapshot for the conversation:read gate (shared by state init + catch-up). */
function canReadSnapshot() {
  return (
    typeof window !== "undefined" &&
    Array.isArray(window.__HELIX_PERMISSIONS__) &&
    window.__HELIX_PERMISSIONS__.includes("conversation:read")
  );
}

function useCanRead() {
  const [canRead, setCanRead] = useState(canReadSnapshot);
  useEffect(() => {
    const sync = (event) => {
      const perms = event.detail?.permissions;
      setCanRead(Array.isArray(perms) && perms.includes("conversation:read"));
    };
    window.addEventListener(MENTION_EVENTS.IDENTITY, sync);
    // Catch-up: the identity dispatch can land between the first render
    // (stale globals snapshot) and this subscription on a warm server —
    // re-read the snapshot so the gate never sticks closed.
    setCanRead(canReadSnapshot());
    return () => window.removeEventListener(MENTION_EVENTS.IDENTITY, sync);
  }, []);
  return canRead;
}

export function MentionsIsland() {
  const canRead = useCanRead();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const panelRef = useRef(null);
  const badgeRef = useRef(null);
  const query = useQuery({
    queryKey: ["mentions"],
    queryFn: fetchMentions,
    enabled: canRead,
    staleTime: Infinity, // refreshes are explicit: open, read, changed
  });
  const mentions = Array.isArray(query.data?.mentions) ? query.data.mentions : [];
  const unread = query.data?.unread_count || 0;

  const refetch = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ["mentions"] });
  }, [queryClient]);

  useEffect(() => {
    const onChanged = () => refetch();
    window.addEventListener(MENTION_EVENTS.CHANGED, onChanged);
    return () => window.removeEventListener(MENTION_EVENTS.CHANGED, onChanged);
  }, [refetch]);

  // Click-outside close (legacy bindSession parity): only while open.
  useEffect(() => {
    if (!open) return undefined;
    const onDocClick = (event) => {
      if (!panelRef.current?.contains(event.target) && !badgeRef.current?.contains(event.target)) {
        setOpen(false);
      }
    };
    document.addEventListener("click", onDocClick);
    return () => document.removeEventListener("click", onDocClick);
  }, [open]);

  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next) refetch(); // legacy toggleMentionsPanel reloads on open
  };

  const jump = (conversationId) => {
    setOpen(false);
    window.dispatchEvent(
      new CustomEvent(MENTION_EVENTS.OPEN_JUMP, { detail: { conversationId } }),
    );
  };

  const markRead = (mentionId) => {
    window.dispatchEvent(new CustomEvent(MENTION_EVENTS.MARK_READ, { detail: { id: mentionId } }));
  };

  const badgeHidden = !canRead || (unread === 0 && !open);
  const badge = (
    <button
      id={MENTION_IDS.badge}
      ref={badgeRef}
      className="mentions-badge"
      type="button"
      title="我的被提及"
      aria-label="我的被提及"
      aria-pressed={open}
      hidden={badgeHidden}
      onClick={toggle}
    >
      <svg className="icon"><use href="/static/icons.svg?v=1.4.0#at-sign" /></svg>
      <span id={MENTION_IDS.count} className={`mentions-count${unread > 0 ? " is-active" : ""}`}>
        {unread}
      </span>
    </button>
  );
  // The portal target lives in index.html; fall back to inline rendering if
  // it is missing so a DOM regression cannot take down the whole island.
  const badgeHost = document.getElementById("mentionsBadgeReactIsland");

  return (
    <>
      {badgeHost ? createPortal(badge, badgeHost) : badge}
      <div ref={panelRef} className="mentions-panel" role="dialog" aria-label="我的被提及" hidden={!open}>
        {open && (
          <>
            <div className="mentions-heading">我的被提及</div>
            {!mentions.length && <div className="queue-empty">暂无被提及</div>}
            {mentions.map((mention) => (
              <div className={`mention-item${mention.unread ? " is-unread" : ""}`} key={mention.id}>
                <div className="mention-top">
                  <button
                    className="mention-link"
                    type="button"
                    data-mention-conversation={mention.conversation_id}
                    onClick={() => jump(mention.conversation_id)}
                  >
                    <strong>{mention.conversation_customer}</strong>
                    <span className="mention-meta">
                      {mention.channel} · {formatTime(mention.created_at)}
                      {mention.unread ? " · 未读" : ""}
                    </span>
                  </button>
                  {mention.unread && (
                    <button
                      className="mention-read"
                      type="button"
                      data-mention-id={mention.id}
                      title="标记已读"
                      aria-label="标记已读"
                      onClick={() => markRead(mention.id)}
                    >
                      <svg className="icon"><use href="/static/icons.svg?v=1.4.0#check" /></svg>
                    </button>
                  )}
                </div>
                <div className="mention-note">{mention.note_preview || "（无内容）"}</div>
                <div className="mention-by">— {mention.mentioned_by}</div>
              </div>
            ))}
          </>
        )}
      </div>
    </>
  );
}

/**
 * Mount the mentions island into a host <div>. Called by the island loader.
 * The badge renders through a portal into #mentionsBadgeReactIsland inside
 * the queue footer; the panel renders into the mount itself.
 * @param {HTMLElement} element - mount point
 */
export function mount(element) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { refetchOnWindowFocus: false } },
  });
  const root = createRoot(element);
  root.render(
    <QueryClientProvider client={queryClient}>
      <MentionsIsland />
    </QueryClientProvider>,
  );
}

export default { mount, MentionsIsland };
