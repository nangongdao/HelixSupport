/**
 * Helix Support — composer island operator tool bars.
 *
 * The canned-response chips, the trailing-/ macro suggest list, the copilot
 * bar and the attachment bar. Split out of composer-island.jsx (400-line
 * module limit); the island root keeps the two forms, all state and every
 * handler, and passes them down — these components stay presentational.
 *
 * Each keeps the legacy element id it replaces (#cannedBar, #macroSuggest,
 * #copilotBar, #attachmentBar …) because the legacy copies are only hidden,
 * not removed, and ui_smoke plus the desktop verifiers locate by them. The
 * one exception is the file input: see INPUT_IDS.attachmentFile.
 */

import React from "react";

import { COMPOSER_EVENTS, INPUT_IDS } from "./constants.js";

export function CannedBar({ toolsVisible, cannedResponses, onPick }) {
  return (
    <div id="cannedBar" className="canned-bar" hidden={!toolsVisible}>
      <span className="canned-label">快捷回复</span>
      <div id="cannedList" className="canned-list">
        {toolsVisible && cannedResponses.length ? (
          cannedResponses.slice(0, 8).map((item) => (
            <button
              key={item.id}
              className="canned-chip"
              type="button"
              data-macro-id={item.id}
              title={item.body}
              onClick={() => onPick(item)}
            >
              {item.title}{item.shortcut ? ` /${item.shortcut}` : ""}
            </button>
          ))
        ) : (
          <span className="canned-empty">暂无快捷回复</span>
        )}
      </div>
    </div>
  );
}

export function MacroSuggest({ options, onPick }) {
  return (
    <div
      id="macroSuggest"
      className="macro-suggest"
      hidden={!options.length}
      role="listbox"
      aria-label="快捷回复建议"
    >
      {options.map((item) => (
        <button
          key={item.id}
          className="macro-option"
          type="button"
          role="option"
          data-macro-id={item.id}
          onClick={() => onPick(item, true)}
        >
          <strong>{item.title}</strong>
          <span>/{item.shortcut || "—"}</span>
        </button>
      ))}
    </div>
  );
}

export function CopilotBar({ toolsVisible, copilot, tone, onSuggest, onTone, onApply }) {
  return (
    <div id="copilotBar" className="copilot-bar" hidden={!toolsVisible}>
      <div className="copilot-tools">
        <button
          id="copilotSuggestBtn"
          className="copilot-btn"
          type="button"
          title="根据对话生成建议回复"
          onClick={onSuggest}
        >
          <svg className="icon"><use href="/static/icons.svg?v=1.4.0#bot" /></svg>智能建议
        </button>
        <select
          id="copilotTone"
          className="copilot-tone"
          aria-label="语气改写"
          value={tone}
          onChange={onTone}
        >
          <option value="">语气改写…</option>
          <option value="friendly">亲切</option>
          <option value="concise">简洁</option>
          <option value="professional">专业</option>
        </select>
        <span id="copilotStatus" className="copilot-status" hidden={!copilot.status}>
          {copilot.status}
        </span>
      </div>
      <div
        id="copilotSuggestions"
        className="copilot-suggestions"
        hidden={!copilot.suggestions.length}
      >
        {copilot.suggestions.map((item, index) => (
          <button
            key={`${index}-${item.content}`}
            type="button"
            className="copilot-suggestion"
            data-index={index}
            title={item.content}
            onClick={() => onApply(item.content)}
          >
            <span className="copilot-suggestion-badge">
              {item.source === "model" ? "AI" : "模板"}
            </span>
            <span className="copilot-suggestion-text">{item.content}</span>
          </button>
        ))}
      </div>
      <div
        id="copilotKnowledge"
        className="copilot-knowledge"
        hidden={!copilot.knowledge.length}
      >
        {copilot.knowledge.map((article) => (
          <button
            key={article.title}
            type="button"
            className="copilot-kb-item"
            data-title={article.title}
            onClick={() => onApply(article.title)}
          >
            <span className="copilot-kb-title">{article.title}</span>
            <span className="copilot-kb-cat">{article.category}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

export function AttachmentBar({ toolsVisible, pendingAttachments, onFileChange }) {
  return (
    <div id="attachmentBar" className="attachment-bar" hidden={!toolsVisible}>
      <div id="pendingAttachments" className="pending-attachments">
        {(pendingAttachments || []).map((item) => (
          <span key={item.id} className="pending-attachment-chip">
            {item.filename}{" "}
            <button
              type="button"
              className="pending-attachment-remove"
              data-id={item.id}
              title="移除"
              onClick={() => {
                window.dispatchEvent(
                  new CustomEvent(COMPOSER_EVENTS.ATTACHMENT_REMOVE, {
                    detail: { id: item.id },
                  }),
                );
              }}
            >
              ×
            </button>
          </span>
        ))}
      </div>
      {/* for/id must be INPUT_IDS.attachmentFile, never the bare legacy
          "attachmentFile": legacy's hidden copy is earlier in tree order, so a
          bare id makes this label control legacy's input and the island's
          onChange bridge never fires. */}
      <label
        className="attachment-upload"
        htmlFor={INPUT_IDS.attachmentFile}
        title="上传附件（图片/PDF/文本）"
      >
        <svg className="icon"><use href="/static/icons.svg?v=1.4.0#file-text" /></svg>
        <span>附件</span>
      </label>
      <input id={INPUT_IDS.attachmentFile} type="file" hidden onChange={onFileChange} />
    </div>
  );
}
