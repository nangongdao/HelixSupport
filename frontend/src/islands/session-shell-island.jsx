/**
 * Helix Support — session/shell domain React island (D3)
 *
 * The shell layer: app header, nav rail, theme/density toggles, and the
 * session identity display. This is the root of the island tree (§D3
 * migration order: leaf→root, session/shell last). The legacy app.js shell
 * stays during dual-track; this island renders a parallel shell mount.
 *
 * See DESKTOP_TAURI_PLAN.md §D3.
 */

import React, { useState, useCallback, useEffect } from "react";
import { createRoot } from "react-dom/client";

const NAV_VIEWS = [
  { id: "workspace", label: "工作台", icon: "inbox" },
  { id: "quality", label: "质量看板", icon: "activity" },
  { id: "knowledge", label: "知识库", icon: "book-open" },
  { id: "admin", label: "管理", icon: "settings" },
  { id: "settings", label: "设置", icon: "sliders" },
];

function SessionShellIsland() {
  const [activeView, setActiveView] = useState("workspace");
  const [theme, setTheme] = useState("dark");
  const [operator, setOperator] = useState("正在验证");

  useEffect(() => {
    // Sync theme with the legacy theme system.
    const root = document.documentElement;
    const currentTheme = root.getAttribute("data-theme") === "light" ? "light" : "dark";
    setTheme(currentTheme);
  }, []);

  const handleNav = useCallback((view) => {
    setActiveView(view);
    window.dispatchEvent(new CustomEvent("helix-nav-switch", { detail: { view } }));
  }, []);

  const toggleTheme = useCallback(() => {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    const root = document.documentElement;
    if (next === "light") root.setAttribute("data-theme", "light");
    else root.removeAttribute("data-theme");
    try { localStorage.setItem("helix-theme", next); } catch { /* */ }
  }, [theme]);

  return (
    <div className="shell-island">
      <header className="app-header">
        <div className="brand-lockup">
          <div className="brand-mark" aria-hidden="true">
            <svg className="icon icon-lg">
              <use href="/static/icons.svg?v=1.3.9#hexagon" />
            </svg>
          </div>
          <div className="brand-copy">
            <strong>Helix Support</strong>
            <span>客服控制台</span>
          </div>
          <span className="environment-chip">CONTROL ROOM</span>
        </div>
        <div className="header-actions">
          <span className="operator-identity">{operator}</span>
          <button
            className="icon-button header-tool"
            type="button"
            title="切换深浅主题"
            aria-label="切换深浅主题"
            onClick={toggleTheme}
          >
            <svg className="icon">
              <use href={`/static/icons.svg?v=1.3.9#${theme === "dark" ? "moon" : "sun"}`} />
            </svg>
          </button>
        </div>
      </header>
      <nav className="app-nav" aria-label="全局导航">
        {NAV_VIEWS.map((item) => (
          <button
            key={item.id}
            className={`nav-item${activeView === item.id ? " is-active" : ""}`}
            type="button"
            data-view={item.id}
            title={item.label}
            aria-label={item.label}
            onClick={() => handleNav(item.id)}
          >
            <svg className="icon">
              <use href={`/static/icons.svg?v=1.3.9#${item.icon}`} />
            </svg>
          </button>
        ))}
      </nav>
    </div>
  );
}

export function mount(element) {
  const root = createRoot(element);
  root.render(<SessionShellIsland />);
}

export default { mount, SessionShellIsland };
