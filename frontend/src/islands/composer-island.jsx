/**
 * Helix Support — composer domain React island (D3 + tools slice)
 *
 * Mirrors the message composer forms AND the operator tool surfaces in the
 * desktop shell. Legacy composer.js owns the send lifecycle (drafts, macros,
 * copilot, shortcuts); the island is the renderer once the legacy forms are
 * yielded. It uses the legacy element ids/aria contract so accessible
 * locators keep working, bridging every interaction back to legacy:
 *   helix-composer-submit             {kind, content}   → legacy send
 *   helix-composer-typing             {kind, content}   → legacy draft/macro hinting
 *   helix-composer-copilot-suggest    {draft}           → fetchCopilotSuggestions
 *   helix-composer-copilot-tone       {tone, text}      → applyCopilotTone
 *   helix-composer-macro-use          {macroId}         → usage tracking
 *   helix-composer-attachment-upload  {file}            → uploadPendingAttachment
 *   helix-composer-attachment-remove  {id}              → pending removal
 * State flows the other way via helix-composer-state snapshots (resolved,
 * human, busy flags, drafts, cannedResponses, pendingAttachments, canOperate)
 * and helix-composer-copilot tool events published by composer.js.
 *
 * The mount point stays hidden in a plain browser tab (legacy is the renderer
 * there); the desktop shell unhides it and yields #customerForm/#operatorForm.
 *
 * See DESKTOP_TAURI_PLAN.md §D3.
 */

import React, { useState, useCallback, useEffect, useRef } from "react";
import { createRoot } from "react-dom/client";

export const COMPOSER_EVENTS = Object.freeze({
  STATE: "helix-composer-state",
  SUBMIT: "helix-composer-submit",
  TYPING: "helix-composer-typing",
  SYNC: "helix-composer-sync",
  COPILOT: "helix-composer-copilot",
  COPILOT_SUGGEST: "helix-composer-copilot-suggest",
  COPILOT_TONE: "helix-composer-copilot-tone",
  MACRO_USE: "helix-composer-macro-use",
  ATTACHMENT_UPLOAD: "helix-composer-attachment-upload",
  ATTACHMENT_REMOVE: "helix-composer-attachment-remove",
});

export const INPUT_IDS = Object.freeze({
  customerForm: "composerFormReact",
  operatorForm: "operatorFormReact",
  customerInput: "composerInputReact",
  operatorInput: "operatorInputReact",
});

export function mount(element) {
  const root = createRoot(element);
  root.render(<ComposerIsland />);
}

/** Mirror of legacy renderMacroSuggest matching (cannedResponses from the
 * composer state snapshot; shows at most six options for a trailing /token). */
export function macroMatches(cannedResponses, query) {
  const needle = (query || "").toLowerCase();
  return (cannedResponses || [])
    .filter((item) => {
      const shortcut = (item.shortcut || "").toLowerCase();
      const title = (item.title || "").toLowerCase();
      return !needle || shortcut.includes(needle) || title.includes(needle);
    })
    .slice(0, 6);
}

export function ComposerIsland() {
  const [state, setState] = useState({
    resolved: false,
    human: false,
    customerBusy: false,
    operatorBusy: false,
    customerDraft: "",
    operatorDraft: "",
    canOperate: false,
    cannedResponses: [],
    pendingAttachments: [],
  });
  const [customerMessage, setCustomerMessage] = useState("");
  const [operatorReply, setOperatorReply] = useState("");
  const [tone, setTone] = useState("");
  const [copilot, setCopilot] = useState({ status: "", suggestions: [], knowledge: [] });
  const lastStateRef = useRef(state);
  const operatorInputRef = useRef(null);

  useEffect(() => {
    const onState = (event) => {
      const next = { ...lastStateRef.current, ...(event.detail || {}) };
      lastStateRef.current = next;
      setState(next);
    };
    const onCopilot = (event) => {
      const detail = event.detail || {};
      setCopilot((prev) => ({
        status: detail.status !== undefined ? detail.status : prev.status,
        suggestions: detail.suggestions !== undefined ? detail.suggestions : prev.suggestions,
        knowledge: detail.knowledge !== undefined ? detail.knowledge : prev.knowledge,
      }));
      // Tone rewrite results land directly in the mirrored textarea.
      if (detail.rewritten !== undefined) setOperatorReply(detail.rewritten);
    };
    window.addEventListener(COMPOSER_EVENTS.STATE, onState);
    window.addEventListener(COMPOSER_EVENTS.COPILOT, onCopilot);
    // Ask legacy for the current composer state on mount (islands mount after
    // the legacy first render).
    window.dispatchEvent(new CustomEvent(COMPOSER_EVENTS.SYNC));
    return () => {
      window.removeEventListener(COMPOSER_EVENTS.STATE, onState);
      window.removeEventListener(COMPOSER_EVENTS.COPILOT, onCopilot);
    };
  }, []);

  // Keep the mirrored textarea values in sync when legacy publishes a draft
  // (e.g. switching conversation loads a saved draft).
  useEffect(() => {
    setCustomerMessage((prev) => (state.customerDraft !== undefined ? state.customerDraft : prev));
  }, [state.customerDraft]);
  useEffect(() => {
    setOperatorReply((prev) => (state.operatorDraft !== undefined ? state.operatorDraft : prev));
  }, [state.operatorDraft]);

  const emitTyping = useCallback((kind, content) => {
    window.dispatchEvent(new CustomEvent(COMPOSER_EVENTS.TYPING, { detail: { kind, content } }));
  }, []);

  const emitSubmit = useCallback((kind, content) => {
    if (!content.trim()) return;
    window.dispatchEvent(new CustomEvent(COMPOSER_EVENTS.SUBMIT, { detail: { kind, content } }));
  }, []);

  const handleCustomerSubmit = useCallback(
    (e) => {
      e.preventDefault();
      if (state.resolved || state.customerBusy) return;
      const content = customerMessage.trim();
      if (!content) return;
      emitSubmit("customer", content);
      setCustomerMessage("");
    },
    [customerMessage, state.resolved, state.customerBusy, emitSubmit],
  );

  const handleOperatorSubmit = useCallback(
    (e) => {
      e.preventDefault();
      if (state.operatorBusy) return;
      const content = operatorReply.trim();
      if (!content) return;
      emitSubmit("operator", content);
      setOperatorReply("");
    },
    [operatorReply, state.operatorBusy, emitSubmit],
  );

  // ── Operator tool surfaces (human + canOperate gated, like legacy) ──
  const toolsVisible = state.human && state.canOperate;

  // Macro suggest: legacy renderMacroSuggest mirrors — match a trailing
  // /token against the canned catalog.
  const tokenMatch = operatorReply.match(/(^|\s)\/([^\s]*)$/);
  const macroOptions = toolsVisible && tokenMatch ? macroMatches(state.cannedResponses, tokenMatch[2]) : [];

  const handleMacroPick = useCallback(
    (macro, viaToken) => {
      setOperatorReply((prev) => {
        const match = prev.match(/(^|\s)\/([^\s]*)$/);
        if (viaToken && match) {
          const start = prev.slice(0, prev.length - match[0].length + (match[1] ? match[1].length : 0));
          return `${start}${macro.body}`;
        }
        return macro.body;
      });
      window.dispatchEvent(new CustomEvent(COMPOSER_EVENTS.MACRO_USE, { detail: { macroId: macro.id } }));
      operatorInputRef.current?.focus();
    },
    [],
  );

  const handleCannedChip = useCallback((macro) => {
    setOperatorReply((prev) => {
      const prefix = prev.trim();
      return prefix ? `${prefix}\n${macro.body}` : macro.body;
    });
    window.dispatchEvent(new CustomEvent(COMPOSER_EVENTS.MACRO_USE, { detail: { macroId: macro.id } }));
    operatorInputRef.current?.focus();
  }, []);

  const handleSuggest = useCallback(() => {
    window.dispatchEvent(
      new CustomEvent(COMPOSER_EVENTS.COPILOT_SUGGEST, { detail: { draft: operatorReply } }),
    );
  }, [operatorReply]);

  const handleTone = useCallback(
    (event) => {
      const selected = event.target.value;
      event.target.value = "";
      setTone("");
      if (selected) {
        window.dispatchEvent(
          new CustomEvent(COMPOSER_EVENTS.COPILOT_TONE, { detail: { tone: selected, text: operatorReply } }),
        );
      }
    },
    [operatorReply],
  );

  const applySuggestion = useCallback((content) => {
    setOperatorReply(content);
    operatorInputRef.current?.focus();
  }, []);

  const handleFileChange = useCallback((event) => {
    const file = event.target.files?.[0];
    if (file) {
      window.dispatchEvent(new CustomEvent(COMPOSER_EVENTS.ATTACHMENT_UPLOAD, { detail: { file } }));
    }
    event.target.value = "";
  }, []);

  return (
    <div className="composer-island">
      <form
        id={INPUT_IDS.customerForm}
        className="composer customer-composer"
        onSubmit={handleCustomerSubmit}
        data-busy={String(state.customerBusy)}
        aria-busy={state.customerBusy}
      >
        <label className="sr-only" htmlFor={INPUT_IDS.customerInput}>客户消息</label>
        <textarea
          id={INPUT_IDS.customerInput}
          rows={2}
          maxLength={4000}
          placeholder="输入一条模拟客户消息…"
          required
          disabled={state.resolved || state.customerBusy}
          value={customerMessage}
          onChange={(e) => {
            setCustomerMessage(e.target.value);
            emitTyping("customer", e.target.value);
          }}
        />
        <button
          className="send-button"
          type="submit"
          title="发送客户消息"
          aria-label="发送客户消息"
          disabled={state.resolved || state.customerBusy}
        >
          <svg className="icon" aria-hidden="true"><use href="/static/icons.svg?v=1.4.0#send" /></svg>
        </button>
      </form>
      <div id="cannedBar" className="canned-bar" hidden={!toolsVisible}>
        <span className="canned-label">快捷回复</span>
        <div id="cannedList" className="canned-list">
          {toolsVisible && state.cannedResponses.length ? (
            state.cannedResponses.slice(0, 8).map((item) => (
              <button
                key={item.id}
                className="canned-chip"
                type="button"
                data-macro-id={item.id}
                title={item.body}
                onClick={() => handleCannedChip(item)}
              >
                {item.title}{item.shortcut ? ` /${item.shortcut}` : ""}
              </button>
            ))
          ) : (
            <span className="canned-empty">暂无快捷回复</span>
          )}
        </div>
      </div>
      <form
        id={INPUT_IDS.operatorForm}
        className="composer operator-composer"
        onSubmit={handleOperatorSubmit}
        data-busy={String(state.operatorBusy)}
        aria-busy={state.operatorBusy}
        hidden={!state.human}
      >
        <label className="sr-only" htmlFor={INPUT_IDS.operatorInput}>人工回复</label>
        <div className="composer-stack">
          <textarea
            id={INPUT_IDS.operatorInput}
            ref={operatorInputRef}
            rows={2}
            maxLength={4000}
            placeholder="输入人工回复… 输入 / 插入快捷回复，Ctrl+Enter 发送"
            disabled={state.operatorBusy}
            value={operatorReply}
            onChange={(e) => {
              setOperatorReply(e.target.value);
              emitTyping("operator", e.target.value);
            }}
          />
          <div id="macroSuggest" className="macro-suggest" hidden={!macroOptions.length} role="listbox" aria-label="快捷回复建议">
            {macroOptions.map((item) => (
              <button
                key={item.id}
                className="macro-option"
                type="button"
                role="option"
                data-macro-id={item.id}
                onClick={() => handleMacroPick(item, true)}
              >
                <strong>{item.title}</strong>
                <span>/{item.shortcut || "—"}</span>
              </button>
            ))}
          </div>
          <div id="copilotBar" className="copilot-bar" hidden={!toolsVisible}>
            <div className="copilot-tools">
              <button id="copilotSuggestBtn" className="copilot-btn" type="button" title="根据对话生成建议回复" onClick={handleSuggest}>
                <svg className="icon"><use href="/static/icons.svg?v=1.4.0#bot" /></svg>智能建议
              </button>
              <select id="copilotTone" className="copilot-tone" aria-label="语气改写" value={tone} onChange={handleTone}>
                <option value="">语气改写…</option>
                <option value="friendly">亲切</option>
                <option value="concise">简洁</option>
                <option value="professional">专业</option>
              </select>
              <span id="copilotStatus" className="copilot-status" hidden={!copilot.status}>{copilot.status}</span>
            </div>
            <div id="copilotSuggestions" className="copilot-suggestions" hidden={!copilot.suggestions.length}>
              {copilot.suggestions.map((item, index) => (
                <button
                  key={`${index}-${item.content}`}
                  type="button"
                  className="copilot-suggestion"
                  data-index={index}
                  title={item.content}
                  onClick={() => applySuggestion(item.content)}
                >
                  <span className="copilot-suggestion-badge">{item.source === "model" ? "AI" : "模板"}</span>
                  <span className="copilot-suggestion-text">{item.content}</span>
                </button>
              ))}
            </div>
            <div id="copilotKnowledge" className="copilot-knowledge" hidden={!copilot.knowledge.length}>
              {copilot.knowledge.map((article) => (
                <button
                  key={article.title}
                  type="button"
                  className="copilot-kb-item"
                  data-title={article.title}
                  onClick={() => applySuggestion(article.title)}
                >
                  <span className="copilot-kb-title">{article.title}</span>
                  <span className="copilot-kb-cat">{article.category}</span>
                </button>
              ))}
            </div>
          </div>
          <div id="attachmentBar" className="attachment-bar" hidden={!toolsVisible}>
            <div id="pendingAttachments" className="pending-attachments">
              {(state.pendingAttachments || []).map((item) => (
                <span key={item.id} className="pending-attachment-chip">
                  {item.filename}{" "}
                  <button
                    type="button"
                    className="pending-attachment-remove"
                    data-id={item.id}
                    title="移除"
                    onClick={() => {
                      window.dispatchEvent(
                        new CustomEvent(COMPOSER_EVENTS.ATTACHMENT_REMOVE, { detail: { id: item.id } }),
                      );
                    }}
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
            <label className="attachment-upload" htmlFor="attachmentFile" title="上传附件（图片/PDF/文本）">
              <svg className="icon"><use href="/static/icons.svg?v=1.4.0#file-text" /></svg>
              <span>附件</span>
            </label>
            <input id="attachmentFile" type="file" hidden onChange={handleFileChange} />
          </div>
          <div className="composer-actions">
            <button
              className="send-button operator-send"
              type="submit"
              title="发送人工回复"
              aria-label="发送人工回复"
              disabled={state.operatorBusy}
            >
              <svg className="icon" aria-hidden="true"><use href="/static/icons.svg?v=1.4.0#send" /></svg>
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}

export default { mount, ComposerIsland };
