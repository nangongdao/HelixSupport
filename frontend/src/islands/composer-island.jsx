/**
 * Helix Support — composer domain React island (D3)
 *
 * Mirrors the message composer forms in the desktop shell. Legacy composer.js
 * owns the send lifecycle (drafts, macros, copilot, shortcuts); the island is
 * the renderer once the legacy forms are yielded. It uses unique DOM ids
 * (composerFormReact…) and keeps the legacy aria-label/button-name contract so
 * accessible locators keep working, bridging every interaction back to legacy:
 *   helix-composer-submit  {kind, content}   → legacy send (customer/operator)
 *   helix-composer-typing  {kind, content}   → legacy draft/macro hinting
 * State flows the other way via helix-composer-state snapshots (resolved,
 * human, busy flags, saved drafts) published by legacy renderDetail().
 *
 * The mount point stays hidden in a plain browser tab (legacy is the renderer
 * there); the desk top shell unhides it and yields #customerForm/#operatorForm.
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

export function ComposerIsland() {
  const [state, setState] = useState({
    resolved: false,
    human: false,
    customerBusy: false,
    operatorBusy: false,
    customerDraft: "",
    operatorDraft: "",
  });
  const [customerMessage, setCustomerMessage] = useState("");
  const [operatorReply, setOperatorReply] = useState("");
  const lastStateRef = useRef(state);

  useEffect(() => {
    const onState = (event) => {
      const next = { ...lastStateRef.current, ...(event.detail || {}) };
      lastStateRef.current = next;
      setState(next);
    };
    window.addEventListener(COMPOSER_EVENTS.STATE, onState);
    // Ask legacy for the current composer state on mount (islands mount after
    // the legacy first render).
    window.dispatchEvent(new CustomEvent(COMPOSER_EVENTS.SYNC));
    return () => window.removeEventListener(COMPOSER_EVENTS.STATE, onState);
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