/**
 * Helix Support — inspector island constants + shared formatters.
 *
 * Split out of inspector-island.jsx (400-line module limit). The bridge
 * event names, the tab catalogue and the escape/URL/time helpers that the
 * sections and the island root both consume.
 */

import React from "react";

export const INSPECTOR_EVENTS = Object.freeze({
  STATE: "helix-inspector-state",
  TAB: "helix-inspector-tab",
  PRIORITY: "helix-inspector-priority",
  LABELS: "helix-inspector-labels",
  // The quality panel is island-rendered but legacy-fed: quality-panel.js
  // publishes the built panel HTML on every loadQualityPanel (island branch).
  QUALITY: "helix-inspector-quality",
  // 生成知识草稿 buttons inside the published HTML delegate the write back
  // to legacy (createKnowledgeDraftFromFeedback) so api()/toast stay there.
  QUALITY_DRAFT: "helix-quality-draft",
  // The note composer is island-owned; the write + completion feedback stay
  // legacy (submitNote) so api()/toast/loadDetail remain in one place.
  NOTE_SUBMIT: "helix-inspector-note-submit",
  NOTE_SUBMITTED: "helix-inspector-note-submitted",
});

export const INSPECTOR_TABS = ["overview", "evidence", "audit", "quality"];
export const TAB_LABELS = { overview: "概览", evidence: "证据", audit: "审计", quality: "质量" };
export const TAB_PANEL_IDS = {
  overview: "inspectorOverview",
  evidence: "inspectorEvidence",
  audit: "inspectorAudit",
  quality: "qualityPanel",
};

export function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/** Only `/`-relative and https:// citation targets are linkable (mirrors
 *  legacy safeCitationUrl so a malicious href degrades to a dead anchor). */
export function safeCitationUrl(value) {
  const url = String(value || "");
  if (url.startsWith("/") || url.startsWith("https://")) return url;
  return "#";
}

export function formatTime(value) {
  if (!value) return "-";
  try {
    return new Date(value).toLocaleString("zh-CN", { hour12: false });
  } catch {
    return String(value);
  }
}

export function latestAssistant(messages) {
  if (!Array.isArray(messages)) return null;
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    if (messages[i] && messages[i].role === "assistant") return messages[i];
  }
  return null;
}

export function renderLabelChips(labels) {
  if (!labels?.length) return <span className="label-empty">无标签</span>;
  return labels.map((label) => <span className="label-chip" key={label}>{escapeHtml(label)}</span>);
}
