/**
 * Helix Support — settings island component tests (D3 long tail)
 *
 * The island owns the settings surface in the desktop shell; the backend
 * info arrives asynchronously via window.__HELIX_BACKEND__ +
 * helix-backend-ready. These tests cover the desktopInfoModel parity with
 * legacy js/desktop-info.js, the async backend-ready tracking, and the
 * rendered legacy id/class contract — no legacy app.js.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import React from "react";

import {
  SettingsIsland,
  SETTINGS_EVENTS,
  SETTINGS_IDS,
  desktopInfoModel,
} from "./settings-island.jsx";

beforeEach(() => {
  delete window.__HELIX_BACKEND__;
  delete window.__TAURI_INTERNALS__;
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("desktopInfoModel parity with js/desktop-info.js", () => {
  it("renders the sidecar readout once the backend has a port", () => {
    // Shell contract (src-tauri/src/lib.rs): { backendPort: location.port }.
    const model = desktopInfoModel({ backendPort: "55954" }, true);
    expect(model.rows).toEqual([
      ["版本", "1.4.0"],
      ["后端端口", "127.0.0.1:55954"],
      ["运行模式", "桌面 sidecar"],
      ["数据目录", "%APPDATA%/HelixSupport/data"],
    ]);
    expect(model.envNoteHidden).toBe(true);
  });

  it("keeps the waiting/error states before and during a failed start", () => {
    const waiting = desktopInfoModel(null, true);
    expect(waiting.rows[1][1]).toBe("等待中…");
    expect(waiting.rows[2][1]).toBe("桌面 sidecar");
    const failed = desktopInfoModel({ error: "spawn failed" }, true);
    expect(failed.rows[2][1]).toBe("错误：spawn failed");
  });

  it("keeps the browser-env placeholders and the visible note", () => {
    const model = desktopInfoModel(null, false);
    expect(model.rows).toEqual([
      ["版本", "1.4.0"],
      ["后端端口", "仅桌面可用"],
      ["运行模式", "浏览器环境"],
      ["数据目录", "仅桌面可用"],
    ]);
    expect(model.envNoteHidden).toBe(false);
  });
});

describe("SettingsIsland", () => {
  function renderIsland() {
    return render(<SettingsIsland />);
  }

  it("shows the browser-env placeholders when neither shell marker exists", () => {
    renderIsland();
    expect(document.getElementById(SETTINGS_IDS.desktopVersion).textContent).toBe("1.4.0");
    expect(document.getElementById(SETTINGS_IDS.desktopBackendPort).textContent).toBe("仅桌面可用");
    expect(document.getElementById(SETTINGS_IDS.desktopBackendMode).textContent).toBe("浏览器环境");
    expect(document.getElementById(SETTINGS_IDS.desktopEnvNote).hidden).toBe(false);
  });

  it("shows 等待中… when the shell exists but the backend is not injected yet", () => {
    // 桌面壳判定与 legacy isDesktop 一致:__TAURI_INTERNALS__ 存在即可,
    // __HELIX_BACKEND__ 未注入时端口保持等待中而非"仅桌面可用"。
    window.__TAURI_INTERNALS__ = { invoke: () => {} };
    renderIsland();
    expect(document.getElementById(SETTINGS_IDS.desktopBackendPort).textContent).toBe("等待中…");
    expect(document.getElementById(SETTINGS_IDS.desktopEnvNote).hidden).toBe(true);
  });

  it("updates the readout when helix-backend-ready lands", () => {
    renderIsland();
    act(() => {
      window.__HELIX_BACKEND__ = { backendPort: "51234" };
      window.dispatchEvent(new CustomEvent(SETTINGS_EVENTS.BACKEND_READY));
    });
    expect(document.getElementById(SETTINGS_IDS.desktopBackendPort).textContent).toBe("127.0.0.1:51234");
    expect(document.getElementById(SETTINGS_IDS.desktopBackendMode).textContent).toBe("桌面 sidecar");
    expect(document.getElementById(SETTINGS_IDS.desktopDataDir).textContent).toBe("%APPDATA%/HelixSupport/data");
  });

  it("seeds from an already-injected backend without waiting for the event", () => {
    window.__HELIX_BACKEND__ = { backendPort: "55954" };
    renderIsland();
    expect(document.getElementById(SETTINGS_IDS.desktopBackendPort).textContent).toBe("127.0.0.1:55954");
  });

  it("keeps the legacy class contract for the desktop axe pass", () => {
    renderIsland();
    expect(document.querySelector(".admin-cards")).toBeTruthy();
    expect(document.querySelectorAll(".admin-card")).toHaveLength(2);
    expect(screen.getByText("桌面运行时")).toBeTruthy();
    expect(screen.getByText("偏好")).toBeTruthy();
    expect(screen.getByText(/主题、密度、低配模式/)).toBeTruthy();
  });
});
