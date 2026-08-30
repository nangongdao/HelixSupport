/**
 * Helix Support — summary banner React island (D3 long tail slice 15)
 *
 * Island-rendered but legacy-fed: js/renderSummaries derives the banner
 * model via js/summary.js and publishes it via helix-summary-state; this
 * island renders the same markup/classes as the legacy #summaryBanner (which
 * the loader hides once the island mounts). The mount stays empty in a plain
 * browser tab (legacy renders there).
 *
 * See DESKTOP_TAURI_PLAN.md §D3.
 */

import React, { useState, useEffect } from "react";
import { createRoot } from "react-dom/client";

export const SUMMARY_EVENT = "helix-summary-state";

export function mount(element) {
  const root = createRoot(element);
  root.render(<SummaryIsland />);
}

export function SummaryIsland() {
  const [model, setModel] = useState({ visible: false, title: "", text: "" });

  useEffect(() => {
    const onState = (event) => {
      setModel(event.detail || { visible: false, title: "", text: "" });
    };
    window.addEventListener(SUMMARY_EVENT, onState);
    window.dispatchEvent(new CustomEvent("helix-summary-sync"));
    return () => window.removeEventListener(SUMMARY_EVENT, onState);
  }, []);

  return (
    <div className="summary-banner" hidden={!model.visible}>
      <span className="csat-label">
        <svg className="icon"><use href="/static/icons.svg?v=1.4.0#file-text" /></svg>
        <span>{model.title}</span>
      </span>
      <div className="summary-text">{model.text}</div>
    </div>
  );
}

export default { mount, SummaryIsland };
