/**
 * Helix Support — inspector island internal-note composer.
 *
 * Split out of inspector-island.jsx (400-line module limit). Island-owned
 * since the note-form yield; ports the legacy mention-suggest UX faithfully
 * (IME-composition guard, caret-based @token matching, keyboard navigation
 * and caret-position replacement — WARNING-3). The write itself stays legacy
 * via helix-inspector-note-submit with a helix-inspector-note-submitted echo.
 */

import React, { useEffect, useRef, useState } from "react";

import { INSPECTOR_EVENTS } from "./helpers.jsx";

/** Mirror of legacy MENTION_PATTERN: an @token at the caret (start-of-text
 * or after whitespace), captured without the @. */
const MENTION_PATTERN = /(?:^|\s)@([A-Za-z0-9._:@/-]*)$/;

export function NoteComposer({ hidden, conversation, collaborators, actorId, canOperate }) {
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
