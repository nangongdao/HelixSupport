/**
 * Helix Support — identity island (D3 long tail: header identity readout).
 *
 * Owns the header identity readout in the desktop shell: "actor · role"
 * (the only data-derived element in the header; the surrounding toggles
 * stay legacy with their own preference lifecycles). Mounts into
 * #identityReactIsland; the legacy #operatorIdentity span is yielded.
 *
 * Data source: app.js publishes window.__HELIX_ROLE__/__HELIX_ACTOR__ and
 * dispatches helix-identity {role, permissions, actorId, tenantId} after
 * every authenticated /api/me (added for the admin island's permission
 * gate) — the island is a pure subscriber, no fetch, no writes.
 *
 * ROLE_LABELS is a verbatim copy of app.js roleLabel's fallback map (the
 * legacy path prefers js/i18n.js with the same zh strings; islands don't
 * wire the i18n module, matching the other islands' verbatim-copy rule).
 *
 * See DESKTOP_TAURI_PLAN.md §D3 (app.js long tail).
 */

import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";

export const IDENTITY_EVENTS = Object.freeze({
  UPDATED: "helix-identity",
});

const ROLE_LABELS = {
  admin: "管理员",
  supervisor: "主管",
  operator: "客服",
  channel: "渠道",
  viewer: "只读",
  auditor: "审计员",
};

/** Legacy initial textContent before /api/me resolves. */
export const PENDING_TEXT = "正在验证";

/**
 * Pure model behind the readout — parity with legacy
 * `els.operatorIdentity.textContent = \`${me.actor_id} · ${roleLabel(me.role)}\``.
 * @param {{actorId: string, role: string}|null} identity
 * @returns {string} readout text; PENDING_TEXT before identity is known
 */
export function identityModel(identity) {
  if (!identity || !identity.actorId) return PENDING_TEXT;
  return `${identity.actorId} · ${ROLE_LABELS[identity.role] || identity.role}`;
}

function currentIdentity() {
  const actorId = (typeof window !== "undefined" && window.__HELIX_ACTOR__) || "";
  const role = (typeof window !== "undefined" && window.__HELIX_ROLE__) || "guest";
  return actorId ? { actorId, role } : null;
}

export function useIdentity() {
  const [identity, setIdentity] = useState(currentIdentity);
  useEffect(() => {
    // refreshAll dispatches helix-identity on every cycle; React skips the
    // re-render when the fields are unchanged.
    const sync = (event) => {
      const detail = event.detail || {};
      if (!detail.actorId) return;
      setIdentity((prev) =>
        prev && prev.actorId === detail.actorId && prev.role === detail.role
          ? prev
          : { actorId: detail.actorId, role: detail.role },
      );
    };
    window.addEventListener(IDENTITY_EVENTS.UPDATED, sync);
    return () => window.removeEventListener(IDENTITY_EVENTS.UPDATED, sync);
  }, []);
  return identity;
}

export function IdentityIsland() {
  const identity = useIdentity();
  return <span className="operator-identity">{identityModel(identity)}</span>;
}

/**
 * Mount the identity island into a host <div>. Called by the island loader.
 * @param {HTMLElement} element - mount point
 */
export function mount(element) {
  const root = createRoot(element);
  root.render(<IdentityIsland />);
}

export default { mount, IdentityIsland };
