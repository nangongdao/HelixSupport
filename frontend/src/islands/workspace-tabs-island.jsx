/**
 * Helix Support — workspace tabs island (D3 long tail).
 *
 * Owns the workspace tablist (队列/工单) in the desktop shell. The pane
 * switching itself — queuePane dataset.mode, ticketPane visibility, ticket
 * loading and the queue refresh — stays in legacy switchWorkspaceTab
 * (js/ticket-view.js); the island renders the two tabs and bridges clicks:
 *   helix-workspace-tab          {field} → legacy switchWorkspaceTab(field)
 *   helix-workspace-tab-changed  {field} ← legacy reports the new active tab
 * (the island applies clicks optimistically and reconciles on the changed
 * event, which also covers programmatic switches like the ticket-jump
 * reset to "queue").
 *
 * The island keeps the legacy class contract (.workspace-tabs/
 * .workspace-tab is-active, role=tab, aria-selected, data-wstab) so the
 * desktop axe pass and the tab indicator CSS keep resolving.
 *
 * See DESKTOP_TAURI_PLAN.md §D3 (app.js long tail).
 */

import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";

export const WORKSPACE_TAB_EVENTS = Object.freeze({
  SWITCH: "helix-workspace-tab",
  CHANGED: "helix-workspace-tab-changed",
});

const TABS = [
  { field: "queue", label: "队列" },
  { field: "tickets", label: "工单" },
];

/** Pure state transition for the tablist. */
export function reduceWorkspaceTabs(state, field) {
  return state === field ? state : field;
}

export function WorkspaceTabsIsland() {
  const [active, setActive] = useState("queue");

  useEffect(() => {
    const onChanged = (event) => {
      const { field } = event.detail || {};
      if (field === "queue" || field === "tickets") setActive(field);
    };
    window.addEventListener(WORKSPACE_TAB_EVENTS.CHANGED, onChanged);
    return () => window.removeEventListener(WORKSPACE_TAB_EVENTS.CHANGED, onChanged);
  }, []);

  return (
    <div className="workspace-tabs" role="tablist" aria-label="工作区视图">
      {TABS.map((tab) => (
        <button
          key={tab.field}
          className={`workspace-tab${active === tab.field ? " is-active" : ""}`}
          type="button"
          role="tab"
          aria-selected={active === tab.field}
          data-wstab={tab.field}
          onClick={() => {
            setActive(reduceWorkspaceTabs(active, tab.field));
            window.dispatchEvent(
              new CustomEvent(WORKSPACE_TAB_EVENTS.SWITCH, { detail: { field: tab.field } }),
            );
          }}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}

/**
 * Mount the workspace tabs island into a host <div>. Called by the island
 * loader.
 * @param {HTMLElement} element - mount point
 */
export function mount(element) {
  const root = createRoot(element);
  root.render(<WorkspaceTabsIsland />);
}

export default { mount, WorkspaceTabsIsland };
