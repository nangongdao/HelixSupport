/**
 * Helix Support — inspector island overview + evidence sections.
 *
 * Split out of inspector-island.jsx (400-line module limit). Both mirror
 * the legacy inspector.js render output (same classes/ids/aria) so the
 * accessible locators and the desktop axe pass keep resolving; the label
 * form and the priority control bridge their writes back to legacy.
 */

import React from "react";

import { escapeHtml, formatTime, renderLabelChips, safeCitationUrl } from "./helpers.jsx";

export function OverviewSection({ conversation, assistant, canOperate, onPriority, handleLabelsSubmit }) {
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

export function EvidenceSection({ citations }) {
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
