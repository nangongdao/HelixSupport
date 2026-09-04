/**
 * Helix Support — admin island identity gate + shared card bits.
 *
 * Split out of admin-island.jsx (400-line module limit). The identity gate
 * is the security-relevant half: every admin query stays disabled until
 * app.js reports admin:manage, so a non-admin island never issues a
 * privileged request (the invariant tests/ui_admin.py asserts).
 */

import React, { useCallback, useEffect, useState } from "react";

import { ADMIN_EVENTS } from "./constants.js";

/* ── identity (deterministic permission gate) ────────────────────────── */

function currentIdentity() {
  return {
    role: (typeof window !== "undefined" && window.__HELIX_ROLE__) || "guest",
    permissions:
      (typeof window !== "undefined" && window.__HELIX_PERMISSIONS__) || [],
    actorId: (typeof window !== "undefined" && window.__HELIX_ACTOR__) || "",
    tenantId:
      (typeof document !== "undefined" && document.documentElement.dataset.tenantId) ||
      (typeof window !== "undefined" && window.__HELIX_TENANT__) ||
      "demo",
  };
}

/**
 * Identity arrives from legacy's /api/me after this island mounts, so
 * globals alone are a race. app.js dispatches helix-identity (detail =
 * {role, permissions, actorId, tenantId}) whenever the authenticated
 * operator changes; until then canManage is false and every query stays
 * disabled — no privileged fetch can fire early.
 */
export function useIdentity() {
  const [identity, setIdentity] = useState(currentIdentity);
  useEffect(() => {
    const sync = (event) =>
      setIdentity(event.detail ? { ...event.detail } : currentIdentity());
    window.addEventListener(ADMIN_EVENTS.IDENTITY, sync);
    return () => window.removeEventListener(ADMIN_EVENTS.IDENTITY, sync);
  }, []);
  return identity;
}

/** admin:manage gates the whole page (app.js canManage parity). */
export function canManageIdentity(identity) {
  return (
    Array.isArray(identity.permissions) &&
    identity.permissions.includes("admin:manage")
  );
}

/* ── shared bits ─────────────────────────────────────────────────────── */

export function useBridge() {
  return useCallback((type, detail) => {
    window.dispatchEvent(new CustomEvent(type, { detail }));
  }, []);
}

export function AdminReadout({ id, rows }) {
  return (
    <dl id={id} className="admin-readout">
      {rows.map(([term, detail]) => (
        <React.Fragment key={term}>
          <dt>{term}</dt>
          <dd>{detail}</dd>
        </React.Fragment>
      ))}
    </dl>
  );
}

export function AdminEmpty({ children }) {
  return <li className="admin-empty">{children}</li>;
}

/**
 * Legacy forms clear their inputs only after a successful write. The
 * bridge reports the outcome through helix-admin-saved; this hook clears
 * the given setters when a matching successful save lands.
 */
export function useClearOnSaved(domains, clear) {
  useEffect(() => {
    const onSaved = (event) => {
      const { ok, domains: savedDomains } = event.detail || {};
      if (!ok) return;
      const wanted = Array.isArray(savedDomains) ? savedDomains : [];
      if (domains.some((domain) => wanted.includes(domain))) clear();
    };
    window.addEventListener(ADMIN_EVENTS.SAVED, onSaved);
    return () => window.removeEventListener(ADMIN_EVENTS.SAVED, onSaved);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}
