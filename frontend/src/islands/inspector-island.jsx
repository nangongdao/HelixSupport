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
 * See DESKTOP_TAURI_PLAN.md §D3.
 */

import React, { useState, useCallback, useEffect, useRef } from "react";
import { createRoot } from "react-dom/client";

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

const INSPECTOR_TABS = ["overview", "evidence", "audit", "quality"];
const TAB_LABELS = { overview: "概览", evidence: "证据", audit: "审计", quality: "质量" };
const TAB_PANEL_IDS = {
  overview: "inspectorOverview",
  evidence: "inspectorEvidence",
  audit: "inspectorAudit",
  quality: "qualityPanel",
};

export function mount(element) {
  const root = createRoot(element);
  root.render(<InspectorIsland />);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/** Only `/`-relative and https:// citation targets are linkable (mirrors
 *  legacy safeCitationUrl so a malicious href degrades to a dead anchor). */
function safeCitationUrl(value) {
  const url = String(value || "");
  if (url.startsWith("/") || url.startsWith("https://")) return url;
  return "#";
}

function formatTime(value) {
  if (!value) return "-";
  try {
    return new Date(value).toLocaleString("zh-CN", { hour12: false });
  } catch {
    return String(value);
  }
}

function OverviewSection({ conversation, assistant, canOperate, onPriority, handleLabelsSubmit }) {
  const metadata = assistant?.metadata || {};
  const confidence = Number.isFinite(Number(metadata.confidence))
    ? Math.round(Number(metadata.confidence) * 100)
    : 0;
  const flags = metadata.risk_categories || [];
  const toolCalls = metadata.tool_calls || [];
  const labels = conversation.labels || [];
  return (
    <>
      <section className="inspector-section">
        <h3>会话</h3>
        <dl className="detail-list">
          <div className="detail-row"><dt>会话 ID</dt><dd>{escapeHtml(conversation.id)}</dd></div>
          <div className="detail-row"><dt>渠道</dt><dd>{escapeHtml(conversation.channel)}</dd></div>
          <div className="detail-row"><dt>客户标识</dt><dd>{escapeHtml(conversation.customer_ref || "未绑定")}</dd></div>
          <div className="detail-row"><dt>优先级</dt><dd>
            <div className="priority-control" role="group" aria-label="调整会话优先级">
              <button className={`priority-option normal${conversation.priority === "normal" ? " is-active" : ""}`} type="button" data-priority="normal" aria-pressed={conversation.priority === "normal"} onClick={() => onPriority("normal")}>普通</button>
              <button className={`priority-option high${conversation.priority === "high" ? " is-active" : ""}`} type="button" data-priority="high" aria-pressed={conversation.priority === "high"} onClick={() => onPriority("high")}>高</button>
            </div>
          </dd></div>
          <div className="detail-row"><dt>标签</dt><dd><div className="overview-labels">{renderLabelChips(labels)}</div></dd></div>
          <div className="detail-row"><dt>分配</dt><dd>{escapeHtml(conversation.assigned_agent || "未分配")}</dd></div>
          <div className="detail-row"><dt>认领</dt><dd>{conversation.claim_active ? `${escapeHtml(conversation.claimed_by)} · 至 ${formatTime(conversation.claim_expires_at)}` : "未认领"}</dd></div>
        </dl>
        {canOperate ? (
          <form id="conversationLabelsForm" className="label-editor" onSubmit={handleLabelsSubmit}>
            <label className="label-editor-field">
              <svg className="icon"><use href="/static/icons.svg?v=1.4.0#tag" /></svg>
              <span className="sr-only">会话标签</span>
              <input name="labels" type="text" maxLength={240} defaultValue={labels.join(", ")} placeholder="VIP, 退款风险" />
            </label>
            <button type="submit" title="保存标签" aria-label="保存标签">
              <svg className="icon"><use href="/static/icons.svg?v=1.4.0#check" /></svg>
            </button>
          </form>
        ) : null}
      </section>
      <section className="inspector-section">
        <h3>最近一次 Agent 运行</h3>
        {assistant ? (
          <>
            <dl className="detail-list">
              <div className="detail-row"><dt>Agent</dt><dd>{escapeHtml(metadata.agent || "-")}</dd></div>
              <div className="detail-row"><dt>意图</dt><dd>{escapeHtml(metadata.intent || "-")}</dd></div>
              <div className="detail-row"><dt>路由模式</dt><dd>{escapeHtml(metadata.route_mode || "-")}</dd></div>
              <div className="detail-row"><dt>质量门</dt><dd>{metadata.quality_approved ? "通过" : "转人工"}</dd></div>
              <div className="detail-row"><dt>置信度</dt><dd>{confidence}%</dd></div>
            </dl>
            <progress className="confidence-progress" max={100} value={confidence}>{confidence}%</progress>
          </>
        ) : <div className="inspector-empty">尚无 Agent 运行记录</div>}
      </section>
      {flags.length ? (
        <section className="inspector-section"><h3>风险标签</h3><div className="trace-flags">{flags.map((flag) => <span className="trace-flag" key={flag}>{escapeHtml(flag)}</span>)}</div></section>
      ) : null}
      {toolCalls.length ? (
        <section className="inspector-section"><h3>工具执行</h3><dl className="detail-list">{toolCalls.map((call) => <div className="detail-row" key={call.tool}><dt>{escapeHtml(call.tool)}</dt><dd>{escapeHtml(call.code)} · {escapeHtml(call.duration_ms)} ms</dd></div>)}</dl></section>
      ) : null}
    </>
  );
}

function EvidenceSection({ citations }) {
  if (!citations.length) {
    return <div className="inspector-empty">本次回答没有知识引用</div>;
  }
  return (
    <section className="inspector-section">
      <h3>已批准知识来源</h3>
      {citations.map((citation) => {
        const href = safeCitationUrl(citation.url);
        const external = href.startsWith("https://") ? ' target="_blank" rel="noreferrer"' : "";
        return (
          <a
            className="citation-item"
            href={href}
            key={citation.id || citation.url}
            {...(external ? { target: "_blank", rel: "noreferrer" } : {})}
            onClick={(e) => {
              if (e.currentTarget.getAttribute("href") === "#") e.preventDefault();
            }}
          >
            <span className="citation-title">{escapeHtml(citation.title || citation.id)}</span>
            <span className="citation-meta">{escapeHtml(citation.url || "内部知识")} · v{escapeHtml(citation.version || "-")}</span>
          </a>
        );
      })}
    </section>
  );
}

/** Mirror of legacy MENTION_PATTERN: an @token at the caret (start-of-text
 * or after whitespace), captured without the @. */
const MENTION_PATTERN = /(?:^|\s)@([A-Za-z0-9._:@/-]*)$/;

/** The internal-note composer (island-owned since the note-form yield).
 * Ports the legacy mention-suggest UX faithfully: IME-composition guard,
 * caret-based @token matching, keyboard navigation and caret-position
 * replacement (WARNING-3); the write itself stays legacy via
 * helix-inspector-note-submit with a helix-inspector-note-submitted echo. */
function NoteComposer({ hidden, conversation, collaborators, actorId, canOperate }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [mentionIndex, setMentionIndex] = useState(-1);
  const [suppress, setSuppress] = useState(false);
  const caretRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    const onSubmitted = (event) => {
      setBusy(false);
      if (event.detail?.ok) {
        setText("");
        setSuppress(false);
        setMentionIndex(-1);
      }
    };
    window.addEventListener(INSPECTOR_EVENTS.NOTE_SUBMITTED, onSubmitted);
    return () => window.removeEventListener(INSPECTOR_EVENTS.NOTE_SUBMITTED, onSubmitted);
  }, []);

  const caret = caretRef.current ?? text.length;
  const head = text.slice(0, caret);
  const tokenMatch = head.match(MENTION_PATTERN);
  const needle = tokenMatch ? tokenMatch[1].toLowerCase() : null;
  const matches =
    canOperate && needle !== null && !suppress
      ? (collaborators || [])
          .filter(
            (c) =>
              c.actor_id !== actorId &&
              (!needle || (c.actor_id || "").toLowerCase().includes(needle)),
          )
          .slice(0, 8)
      : [];
  const mentionOpen = matches.length > 0;

  const handleInput = (event) => {
    // IME 组合期间(中间拼音/片假名)不渲染也不收起;避免候选列表干扰选字
    // (中文客服台第一优先,HIGH-1 修复)。
    if (event.nativeEvent.isComposing) return;
    caretRef.current = event.target.selectionStart;
    setText(event.target.value);
    setMentionIndex(-1);
    setSuppress(false);
  };

  const applyMention = (pickedActor) => {
    const ta = inputRef.current;
    if (!ta) return;
    // apply 时以当前 value + caret 重新推导 @token 段,不信任 input 时捕获的
    // 位置——用户可能已用鼠标移动光标(WARNING-3 修复)。
    const atCaret = ta.selectionStart ?? ta.value.length;
    const headNow = ta.value.slice(0, atCaret);
    const match = headNow.match(MENTION_PATTERN);
    if (!match) {
      setSuppress(true);
      setMentionIndex(-1);
      return;
    }
    const start = atCaret - match[0].length + match[0].lastIndexOf("@");
    if (start < 0) {
      setSuppress(true);
      return;
    }
    const next = `${ta.value.slice(0, start)}@${pickedActor} ${ta.value.slice(atCaret)}`;
    const pos = start + pickedActor.length + 2;
    setText(next);
    caretRef.current = pos;
    setSuppress(false);
    setMentionIndex(-1);
    requestAnimationFrame(() => {
      if (inputRef.current) {
        inputRef.current.setSelectionRange(pos, pos);
        inputRef.current.focus();
      }
    });
  };

  const handleKeyDown = (event) => {
    if (event.nativeEvent.isComposing) return;
    if (!mentionOpen) return;
    if (event.key === "Escape") {
      event.preventDefault();
      setSuppress(true);
      setMentionIndex(-1);
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const delta = event.key === "ArrowDown" ? 1 : -1;
      setMentionIndex((prev) => {
        const nextIndex = prev < 0 ? (delta > 0 ? 0 : matches.length - 1) : prev + delta;
        return (nextIndex + matches.length) % matches.length;
      });
      return;
    }
    if (event.key === "Enter" || event.key === "Tab") {
      if (mentionIndex >= 0 && matches[mentionIndex]) {
        event.preventDefault();
        applyMention(matches[mentionIndex].actor_id);
      }
    }
  };

  const handleSubmit = (event) => {
    event.preventDefault();
    const content = text.trim();
    if (!content || busy) return;
    setBusy(true);
    window.dispatchEvent(
      new CustomEvent(INSPECTOR_EVENTS.NOTE_SUBMIT, { detail: { content } }),
    );
  };

  return (
    <form id="noteForm" className="note-composer" hidden={hidden} onSubmit={handleSubmit}>
      <label htmlFor="noteInput">
        内部备注 <span>仅团队可见</span>
      </label>
      <div className="note-input-row">
        <div
          id="mentionSuggest"
          className="macro-suggest"
          hidden={!mentionOpen}
          role="listbox"
          aria-label="坐席提及候选"
        >
          {matches.map((c, index) => (
            <button
              key={c.actor_id}
              className={`macro-option${index === mentionIndex ? " is-active" : ""}`}
              type="button"
              role="option"
              data-mention-actor={c.actor_id}
              data-mention-index={index}
              onClick={() => applyMention(c.actor_id)}
            >
              <strong>{c.actor_id}</strong>
              <span>{c.roleLabel}</span>
            </button>
          ))}
        </div>
        <textarea
          id="noteInput"
          ref={inputRef}
          rows={2}
          maxLength={4000}
          placeholder="记录核对结果或交接信息，@ 提及同事"
          required
          value={text}
          onChange={handleInput}
          onKeyDown={handleKeyDown}
        />
        <button type="submit" title="添加内部备注" aria-label="添加内部备注" disabled={busy}>
          <svg className="icon" aria-hidden="true"><use href="/static/icons.svg?v=1.4.0#send" /></svg>
        </button>
      </div>
    </form>
  );
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

function latestAssistant(messages) {
  if (!Array.isArray(messages)) return null;
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    if (messages[i] && messages[i].role === "assistant") return messages[i];
  }
  return null;
}

function renderLabelChips(labels) {
  if (!labels?.length) return <span className="label-empty">无标签</span>;
  return labels.map((label) => <span className="label-chip" key={label}>{escapeHtml(label)}</span>);
}

export default { mount, InspectorIsland };