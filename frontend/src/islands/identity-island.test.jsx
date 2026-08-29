/**
 * Helix Support — identity island component tests (D3 long tail)
 *
 * The island is a pure helix-identity subscriber rendering the header
 * readout "actor · role". These tests cover the model parity with legacy
 * roleLabel, the pending placeholder, and the subscription contract — no
 * legacy app.js.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import React from "react";

import {
  IdentityIsland,
  IDENTITY_EVENTS,
  identityModel,
  PENDING_TEXT,
} from "./identity-island.jsx";

function publish(detail) {
  act(() => {
    window.dispatchEvent(new CustomEvent(IDENTITY_EVENTS.UPDATED, { detail }));
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  delete window.__HELIX_ACTOR__;
  delete window.__HELIX_ROLE__;
});

describe("identityModel parity with legacy roleLabel", () => {
  it("formats actor · role with the legacy label map", () => {
    expect(identityModel({ actorId: "demo.admin", role: "admin" })).toBe("demo.admin · 管理员");
    expect(identityModel({ actorId: "op-2", role: "operator" })).toBe("op-2 · 客服");
    expect(identityModel({ actorId: "aud-1", role: "auditor" })).toBe("aud-1 · 审计员");
  });

  it("falls back to the raw role code like legacy", () => {
    expect(identityModel({ actorId: "svc", role: "channel" })).toBe("svc · 渠道");
    expect(identityModel({ actorId: "x", role: "future_role" })).toBe("x · future_role");
  });

  it("keeps the legacy pending text before identity is known", () => {
    expect(identityModel(null)).toBe(PENDING_TEXT);
    expect(identityModel({ actorId: "", role: "admin" })).toBe(PENDING_TEXT);
    expect(PENDING_TEXT).toBe("正在验证");
  });
});

describe("IdentityIsland", () => {
  it("shows the pending text until the first helix-identity lands", () => {
    render(<IdentityIsland />);
    expect(screen.getByText("正在验证")).toBeTruthy();
    publish({ role: "admin", permissions: ["admin:manage"], actorId: "demo.admin", tenantId: "demo" });
    expect(screen.getByText("demo.admin · 管理员")).toBeTruthy();
  });

  it("seeds from already-published globals without waiting for the event", () => {
    window.__HELIX_ACTOR__ = "demo.admin";
    window.__HELIX_ROLE__ = "admin";
    render(<IdentityIsland />);
    expect(screen.getByText("demo.admin · 管理员")).toBeTruthy();
  });

  it("keeps the legacy operator-identity class", () => {
    const { container } = render(<IdentityIsland />);
    expect(container.querySelector("span.operator-identity")).toBeTruthy();
  });
});
