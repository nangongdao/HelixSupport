/**
 * Helix Support — inspector domain React island (D3)
 *
 * Migrates the inspector tab surface to React, using useReducer with the
 * existing createInspectorState/reduceInspector from js/inspector.js (§43.6).
 * Renders the tab navigation + panel containers; the tab content stays in
 * the legacy controller during dual-track (it depends on detail data and
 * SSE events).
 *
 * See DESKTOP_TAURI_PLAN.md §D3.
 */

import React, { useReducer } from "react";
import { createRoot } from "react-dom/client";

const INSPECTOR_TABS = ["overview", "evidence", "audit", "quality"];
const TAB_LABELS = { overview: "概览", evidence: "证据", audit: "审计", quality: "质量" };

function createInspectorState() {
  return {
    activeTab: "overview",
    collapsed: false,
    rendered: { overview: false, evidence: false, audit: false },
  };
}

function reduceInspector(state, action) {
  switch (action.type) {
    case "select-tab": {
      if (!INSPECTOR_TABS.includes(action.tab)) return state;
      return { ...state, activeTab: action.tab };
    }
    case "set-collapsed": {
      if (typeof action.collapsed !== "boolean" || action.collapsed === state.collapsed) return state;
      return { ...state, collapsed: action.collapsed };
    }
    default:
      return state;
  }
}

function InspectorIsland() {
  const [state, dispatch] = useReducer(reduceInspector, undefined, createInspectorState);

  return (
    <aside className="inspector-surface" aria-label="会话检查器">
      <nav className="inspector-tabs" role="tablist" aria-label="会话信息">
        {INSPECTOR_TABS.map((tab) => (
          <button
            key={tab}
            className={`inspector-tab${state.activeTab === tab ? " is-active" : ""}`}
            type="button"
            role="tab"
            aria-selected={state.activeTab === tab}
            data-tab={tab}
            onClick={() => dispatch({ type: "select-tab", tab })}
          >
            {TAB_LABELS[tab]}
          </button>
        ))}
      </nav>
      {INSPECTOR_TABS.filter((t) => t !== "quality").map((tab) => (
        <div
          key={tab}
          id={`inspector${tab.charAt(0).toUpperCase() + tab.slice(1)}`}
          className="inspector-panel"
          role="tabpanel"
          hidden={state.activeTab !== tab}
        />
      ))}
    </aside>
  );
}

export function mount(element) {
  const root = createRoot(element);
  root.render(<InspectorIsland />);
}

export default { mount, InspectorIsland };
