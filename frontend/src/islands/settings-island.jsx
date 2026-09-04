/**
 * Helix Support — settings island (D3 long tail: settings/desktop-info).
 *
 * Owns the settings surface (#placeholderView) in the desktop shell: the
 * desktop runtime readout (version/port/mode/data dir) and the preferences
 * card. Mounts into #settingsReactIsland; the two legacy .admin-card
 * sections are yielded (hidden) while the mount exists.
 *
 * Desktop backend info arrives asynchronously — the shell injects
 * window.__HELIX_BACKEND__ and dispatches helix-backend-ready when the
 * sidecar origin takes over — so the island tracks both the current value
 * and the event, exactly what legacy loadDesktopInfo() re-runs on every
 * view switch (js/desktop-info.js). In a browser tab the island never
 * mounts; the legacy cards stay the only renderer.
 *
 * The island keeps the legacy class/id contract for the readout fields via
 * React-suffixed ids (the yielded legacy tree keeps the originals) and the
 * same .admin-card classes so the desktop axe pass keeps resolving.
 *
 * See DESKTOP_TAURI_PLAN.md §D3 (app.js long tail).
 */

import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { version as DESKTOP_VERSION } from "../../package.json";

export const SETTINGS_EVENTS = Object.freeze({
  BACKEND_READY: "helix-backend-ready",
});

/** React-suffixed ids: the yielded legacy cards keep the originals. */
export const SETTINGS_IDS = Object.freeze({
  desktopInfoReadout: "desktopInfoReadoutReact",
  desktopVersion: "desktopVersionReact",
  desktopBackendPort: "desktopBackendPortReact",
  desktopBackendMode: "desktopBackendModeReact",
  desktopDataDir: "desktopDataDirReact",
  desktopEnvNote: "desktopEnvNoteReact",
});

/**
 * Pure model behind the desktop runtime readout — semantics from
 * js/desktop-info.js loadDesktopInfo (§43.6 framework-agnostic; window
 * reads replaced by parameters).
 *
 * The shell's injection contract (src-tauri/src/lib.rs) is
 * `{ backendPort: location.port }` on success and `{ error: "…" }` on
 * failure. Legacy read `backend.port`, which the shell never injects, so
 * the settings port readout had shown "等待中…" since D1 — this model
 * reads the real field.
 * @param {Object|null} backend - window.__HELIX_BACKEND__ (null in browser)
 * @param {boolean} isDesktop - Tauri shell or sidecar-injected backend
 * @returns {{rows: [string, string][], envNoteHidden: boolean}}
 */
export function desktopInfoModel(backend, isDesktop) {
  if (isDesktop) {
    const info = backend || {};
    return {
      rows: [
        ["版本", DESKTOP_VERSION],
        ["后端端口", info.backendPort ? `127.0.0.1:${info.backendPort}` : "等待中…"],
        ["运行模式", info.error ? `错误：${info.error}` : "桌面 sidecar"],
        ["数据目录", "%APPDATA%/HelixSupport/data"],
      ],
      envNoteHidden: true,
    };
  }
  return {
    rows: [
      ["版本", DESKTOP_VERSION],
      ["后端端口", "仅桌面可用"],
      ["运行模式", "浏览器环境"],
      ["数据目录", "仅桌面可用"],
    ],
    envNoteHidden: false,
  };
}

/**
 * The shell injects __HELIX_BACKEND__ on page load and fires
 * helix-backend-ready once the sidecar origin is serving. The island can
 * mount before or after either, so seed from the current globals and then
 * re-read on the event.
 */
export function useDesktopBackend() {
  const read = () => {
    const isDesktop =
      typeof window !== "undefined" &&
      (window.__HELIX_BACKEND__ !== undefined || "__TAURI_INTERNALS__" in window);
    return { backend: window.__HELIX_BACKEND__ || null, isDesktop };
  };
  const [state, setState] = useState(read);
  useEffect(() => {
    const sync = () => setState(read());
    window.addEventListener(SETTINGS_EVENTS.BACKEND_READY, sync);
    return () => window.removeEventListener(SETTINGS_EVENTS.BACKEND_READY, sync);
  }, []);
  return state;
}

export function SettingsIsland() {
  const { backend, isDesktop } = useDesktopBackend();
  const model = desktopInfoModel(backend, isDesktop);
  return (
    <div className="admin-cards">
      <section className="admin-card" aria-label="桌面运行时">
        <h3>桌面运行时</h3>
        <dl id={SETTINGS_IDS.desktopInfoReadout} className="admin-readout" aria-live="polite">
          {model.rows.map(([term, detail]) => (
            <React.Fragment key={term}>
              <dt>{term}</dt>
              <dd id={term === "版本" ? SETTINGS_IDS.desktopVersion
                : term === "后端端口" ? SETTINGS_IDS.desktopBackendPort
                : term === "运行模式" ? SETTINGS_IDS.desktopBackendMode
                : SETTINGS_IDS.desktopDataDir}
              >
                {detail}
              </dd>
            </React.Fragment>
          ))}
        </dl>
        <p className="admin-field" id={SETTINGS_IDS.desktopEnvNote} hidden={model.envNoteHidden}>
          当前运行于浏览器环境，桌面运行时信息仅在 Tauri 桌面壳内可见。
        </p>
      </section>
      <section className="admin-card" aria-label="偏好">
        <h3>偏好</h3>
        <p className="admin-field">主题、密度、低配模式等偏好已集成在顶部工具栏，可随时切换并自动持久化。</p>
      </section>
    </div>
  );
}

/**
 * Mount the settings island into a host <div>. Called by the island loader.
 * @param {HTMLElement} element - mount point
 */
export function mount(element) {
  const root = createRoot(element);
  root.render(<SettingsIsland />);
}

export default { mount, SettingsIsland };
