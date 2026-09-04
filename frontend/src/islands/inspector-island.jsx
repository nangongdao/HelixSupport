/**
 * Helix Support — inspector domain React island (D3)
 *
 * Mirrors the inspector surface in the desktop shell. The legacy
 * inspector.js owns the render from detail (pure functions) and the update
 * lifecycle (labels/priority via API). Once the island mounts, legacy
 * publishes detail snapshots via helix-inspector-state and yields its
 * panels; this island renders the tabs + overview/evidence/audit panels
 * with the same ids/aria contract (so accessible locators keep working)
 * and bridges interactions back to legacy:
 *   helix-inspector-tab     {tab}                  → switchInspectorTab
 *   helix-inspector-priority {priority}            → updatePriority
 *   helix-inspector-labels  {labels}               → updateLabels
 *   helix-inspector-submit  {kind, content}        → (customer/operator send)
 * The quality tab panel is island-rendered but legacy-fed: quality-panel.js
 * publishes the built panel HTML via helix-inspector-quality, and the
 * 生成知识草稿 buttons inside it delegate back via helix-quality-draft.
 * Mounts into #inspectorReactIsland; the mount stays hidden in a plain
 * browser tab (legacy renders there).
 *
 * This file is the composition root: the constants/formatters, the overview
 * and evidence sections, and the note composer live under ./inspector/
 * (the domain outgrew the project's 400-line module limit).
 *
 * See DESKTOP_TAURI_PLAN.md §D3.
 */

import React, { useState, useCallback, useEffect, useRef } from "react";
import { createRoot } from "react-dom/client";

import {
  INSPECTOR_EVENTS,
  INSPECTOR_TABS,
  TAB_LABELS,
  TAB_PANEL_IDS,
  escapeHtml,
  formatTime,
  latestAssistant,
} from "./inspector/helpers.jsx";
import { EvidenceSection, OverviewSection } from "./inspector/sections.jsx";
import { NoteComposer } from "./inspector/note-composer.jsx";

export { INSPECTOR_EVENTS } from "./inspector/helpers.jsx";

export function mount(element) {
  const root = createRoot(element);
  root.render(<InspectorIsland />);
}

export function InspectorIsland() {
  const [state, setState] = useState({ detail: null, collapsed: false, activeTab: "overview" });
  const [quality, setQuality] = useState(null);
  const lastStateRef = useRef(state);

  useEffect(() => {
    const onState = (event) => {
      const next = { ...lastStateRef.current, ...(event.detail || {}) };
      lastStateRef.current = next;
      setState(next);
    };
    const onQuality = (event) => {
      setQuality(event.detail || null);
    };
    window.addEventListener(INSPECTOR_EVENTS.STATE, onState);
    window.addEventListener(INSPECTOR_EVENTS.QUALITY, onQuality);
    window.dispatchEvent(new CustomEvent("helix-inspector-sync"));
    return () => {
      window.removeEventListener(INSPECTOR_EVENTS.STATE, onState);
      window.removeEventListener(INSPECTOR_EVENTS.QUALITY, onQuality);
    };
  }, []);

  const { detail, collapsed, activeTab } = { ...state, activeTab: state.activeTab || "overview" };
  const conversation = detail?.conversation || null;

  const handleTab = useCallback((tab) => {
    setState((prev) => ({ ...prev, activeTab: tab }));
    window.dispatchEvent(new CustomEvent(INSPECTOR_EVENTS.TAB, { detail: { tab } }));
  }, []);

  const handlePriority = useCallback((priority) => {
    window.dispatchEvent(new CustomEvent(INSPECTOR_EVENTS.PRIORITY, { detail: { priority } }));
  }, []);

  const handleLabelsSubmit = useCallback((event) => {
    event.preventDefault();
    const input = event.currentTarget.elements.labels;
    if (!input) return;
    const labels = input.value
      .split(/[,，]/)
      .map((label) => label.trim())
      .filter(Boolean);
    window.dispatchEvent(new CustomEvent(INSPECTOR_EVENTS.LABELS, { detail: { labels } }));
  }, []);

  // Delegate 生成知识草稿 clicks inside the published quality HTML back to
  // legacy (the island never issues the write itself).
  const handleQualityClick = useCallback((event) => {
    const button = event.target.closest?.(".quality-gap-draft");
    if (!button) return;
    window.dispatchEvent(
      new CustomEvent(INSPECTOR_EVENTS.QUALITY_DRAFT, {
        detail: {
          conversationId: button.dataset.conversationId,
          messageId: button.dataset.messageId,
        },
      }),
    );
  }, []);

  if (collapsed) return null;

  return (
    <aside className="inspector-surface" aria-label="会话检查器">
      <nav id="inspectorTabs" className="inspector-tabs" role="tablist" aria-label="会话信息">
        {INSPECTOR_TABS.map((tab) => (
          <button
            key={tab}
            className={`inspector-tab${activeTab === tab ? " is-active" : ""}`}
            type="button"
            role="tab"
            aria-selected={activeTab === tab}
            data-tab={tab}
            onClick={() => handleTab(tab)}
          >
            <svg className="icon"><use href={`/static/icons.svg?v=1.4.0#${tab === "quality" ? "activity" : tab === "evidence" ? "book-open" : tab === "audit" ? "shield-check" : "activity"}`} /></svg>
            <span>{TAB_LABELS[tab]}</span>
          </button>
        ))}
      </nav>
      {INSPECTOR_TABS.map((tab) => {
        const hidden = activeTab !== tab;
        // The quality panel renders the legacy-built aggregates regardless of
        // the selected conversation (the dashboard is conversation-independent,
        // same as the legacy panel); the HTML arrives via helix-inspector-quality.
        if (tab === "quality") {
          return (
            <div
              key={tab}
              id={TAB_PANEL_IDS[tab]}
              className="quality-panel inspector-panel"
              role="tabpanel"
              aria-label="质量看板"
              hidden={hidden}
              onClick={handleQualityClick}
            >
              {quality ? (
                <>
                  <div
                    className="quality-buckets"
                    aria-live="polite"
                    dangerouslySetInnerHTML={{ __html: quality.bucketsHtml || "" }}
                  />
                  <div className="quality-gaps-block">
                    <div
                      className="quality-gaps"
                      aria-live="polite"
                      dangerouslySetInnerHTML={{ __html: quality.gapsHtml || "" }}
                    />
                  </div>
                </>
              ) : (
                <div className="inspector-empty">暂无质量数据。处理一些会话后会在此汇总。</div>
              )}
            </div>
          );
        }
        if (!conversation) {
          return <div key={tab} id={TAB_PANEL_IDS[tab]} className="inspector-panel" role="tabpanel" hidden={hidden} />;
        }
        if (tab === "overview") {
          return (
            <div key={tab} id={TAB_PANEL_IDS[tab]} className="inspector-panel" role="tabpanel" hidden={hidden}>
              <OverviewSection conversation={conversation} assistant={detail?.messages ? latestAssistant(detail.messages) : null} canOperate={state.canOperate} onPriority={handlePriority} handleLabelsSubmit={handleLabelsSubmit} />
            </div>
          );
        }
        if (tab === "evidence") {
          const assistant = detail?.messages ? latestAssistant(detail.messages) : null;
          const citations = assistant?.metadata?.citations || [];
          return (
            <div key={tab} id={TAB_PANEL_IDS[tab]} className="inspector-panel" role="tabpanel" hidden={hidden}>
              <EvidenceSection citations={citations} />
            </div>
          );
        }
        if (tab === "audit") {
          const events = (detail?.audit_events || []).slice(-20);
          return (
            <div key={tab} id={TAB_PANEL_IDS[tab]} className="inspector-panel" role="tabpanel" hidden={hidden}>
              {events.length ? <div className="audit-list">{events.map((event) => (
                <article className="audit-item" key={event.id || event.created_at}>
                  <div className="audit-meta"><strong>{escapeHtml(event.event_type)}</strong><time dateTime={escapeHtml(event.created_at)}>{formatTime(event.created_at)}</time></div>
                  <div className="audit-actor">{escapeHtml(event.actor)}{event.request_id ? ` · ${escapeHtml(event.request_id)}` : ""}</div>
                </article>
              ))}</div> : <div className="inspector-empty">暂无审计事件</div>}
            </div>
          );
        }
        return null;
      })}
      <NoteComposer
        hidden={!conversation || conversation.status === "resolved"}
        conversation={conversation}
        collaborators={state.collaborators}
        actorId={state.actorId}
        canOperate={Boolean(state.canOperate)}
      />
    </aside>
  );
}
