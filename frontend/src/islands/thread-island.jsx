/**
 * Helix Support — message thread React island (D3 long tail glue slice)
 *
 * Island-rendered but legacy-fed: js/thread.js owns the loadDetail /
 * loadOlderMessages lifecycle and the feedback/translate write paths, and
 * publishes thread snapshots via helix-thread-state. This island mirrors
 * the legacy transcript DOM (same classes/aria contract) and bridges the
 * interactions back:
 *   helix-thread-load-older  {}                     → loadOlderMessages
 *   helix-thread-feedback    {messageId, rating}    → recordFeedback
 *   helix-thread-translate   {messageId, language}  → requestTranslation
 * receiving helix-thread-feedback-recorded / -translate-result completions.
 * Mounts into #threadReactIsland; the mount stays empty in a plain browser
 * tab (legacy #messages renders there).
 *
 * See DESKTOP_TAURI_PLAN.md §D3.
 */

import React, { useState, useEffect, useRef } from "react";
import { createRoot } from "react-dom/client";

export const THREAD_EVENTS = Object.freeze({
  STATE: "helix-thread-state",
  LOAD_OLDER: "helix-thread-load-older",
  FEEDBACK: "helix-thread-feedback",
  FEEDBACK_RECORDED: "helix-thread-feedback-recorded",
  TRANSLATE: "helix-thread-translate",
  TRANSLATE_RESULT: "helix-thread-translate-result",
});

const ROLE_NAMES = {
  customer: "客户",
  assistant: "自动客服",
  operator: "人工客服",
  internal_note: "内部备注",
};

export function mount(element) {
  const root = createRoot(element);
  root.render(<ThreadIsland container={element} />);
}

function formatTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

/** @token mentions render as chips — same shape as the legacy escape+replace,
 * with React escaping the text nodes. */
function renderContentWithMentions(content) {
  const pattern = /@([A-Za-z0-9._:@/-]{2,64})/g;
  const parts = [];
  let last = 0;
  let key = 0;
  let match;
  while ((match = pattern.exec(content))) {
    if (match.index > last) parts.push(content.slice(last, match.index));
    parts.push(
      <span className="mention-chip" key={key++}>
        @{match[1]}
      </span>,
    );
    last = match.index + match[0].length;
  }
  if (last < content.length) parts.push(content.slice(last));
  return parts;
}

function AttachmentChip({ id, meta }) {
  const name = meta?.filename || id;
  const href = `/api/attachments/${encodeURIComponent(id)}/download`;
  // 图片(安全子集,排除 svg)渲染懒加载缩略图预览;其余走文件下载链接。
  const isImage = /^image\/(png|jpe?g|gif|webp)$/.test(meta?.content_type || "");
  if (isImage) {
    return (
      <a className="attachment-chip is-image" href={href} data-id={id} target="_blank" rel="noopener" title={`${name} — 点击打开预览`}>
        <img className="attachment-thumb" src={href} alt="" loading="lazy" />
        <span className="attachment-chip-name">{name}</span>
      </a>
    );
  }
  return (
    <a className="attachment-chip" href={href} data-id={id} target="_blank" rel="noopener" title={`下载 ${name}`}>
      <svg className="icon" aria-hidden="true"><use href="/static/icons.svg?v=1.4.0#file-text" /></svg>
      <span className="attachment-chip-name">{name}</span>
    </a>
  );
}

/** Per-message translate bar with its own select state and inline result. */
function TranslateBar({ messageId, languages, resultHtml, onTranslate }) {
  const [language, setLanguage] = useState(languages[0]?.code || "en");
  return (
    <div className="translate-bar" data-message-id={messageId}>
      <select
        className="translate-lang"
        aria-label="翻译目标语言"
        value={language}
        onChange={(event) => setLanguage(event.target.value)}
      >
        {languages.map((entry) => (
          <option key={entry.code} value={entry.code}>
            {entry.name}
          </option>
        ))}
      </select>
      <button className="translate-button" type="button" onClick={() => onTranslate(messageId, language)}>
        翻译
      </button>
      <span className="translate-result" dangerouslySetInnerHTML={{ __html: resultHtml || "" }} />
    </div>
  );
}

function MessageRow({ message, isReply, snapshot, feedbackRating, translateHtml, onFeedback, onTranslate }) {
  const metadata = message.metadata || {};
  const role = ROLE_NAMES[message.role] || message.role;
  const attachmentIds = metadata.attachment_ids || [];
  return (
    <div className={`message-row ${message.role}${isReply ? " is-reply" : ""}`}>
      <div className="message-card">
        <div className="message-meta">
          <span>{role}</span>
          {message.role === "assistant" && metadata.agent ? (
            <span className="agent-chip">{metadata.agent}</span>
          ) : null}
          {isReply ? (
            <span className="note-reply-mark" title={`回复了 ${message.reply_to}`}>
              ↳ 回复
            </span>
          ) : null}
          <time dateTime={message.created_at}>{formatTime(message.created_at)}</time>
        </div>
        <div className="message-bubble">{renderContentWithMentions(message.content)}</div>
        {attachmentIds.length ? (
          <div className="message-attachments">
            {attachmentIds.map((id) => (
              <AttachmentChip key={id} id={id} meta={snapshot.attachmentMeta?.[id]} />
            ))}
          </div>
        ) : null}
        {message.role === "assistant" ? (
          <div className="feedback-actions" aria-label="回答反馈">
            {[1, -1].map((rating) => {
              const recorded = feedbackRating === rating;
              return (
                <button
                  key={rating}
                  className={`feedback-button${recorded ? " is-recorded" : ""}`}
                  type="button"
                  data-feedback={rating}
                  data-message-id={message.id}
                  title={recorded ? "已记录" : rating === 1 ? "有帮助" : "需改进"}
                  aria-label={rating === 1 ? "有帮助" : "需改进"}
                  aria-pressed={recorded ? "true" : "false"}
                  onClick={() => onFeedback(message.id, rating)}
                >
                  <svg className="icon" aria-hidden="true">
                    <use href={`/static/icons.svg?v=1.4.0#${rating === 1 ? "thumbs-up" : "thumbs-down"}`} />
                  </svg>
                </button>
              );
            })}
          </div>
        ) : null}
        {message.role === "customer" && snapshot.canTranslate ? (
          <TranslateBar
            messageId={message.id}
            languages={snapshot.languages}
            resultHtml={translateHtml}
            onTranslate={onTranslate}
          />
        ) : null}
      </div>
    </div>
  );
}

export function ThreadIsland({ container }) {
  const [snapshot, setSnapshot] = useState(null);
  const [feedbackByMessage, setFeedbackByMessage] = useState({});
  const [translations, setTranslations] = useState({});
  // Scroll anchor bookkeeping (mirrors the legacy preserveAnchor math).
  const prevHeightRef = useRef(0);

  useEffect(() => {
    const onState = (event) => setSnapshot(event.detail || null);
    const onFeedbackRecorded = (event) => {
      const { messageId, rating, ok } = event.detail || {};
      if (!messageId || !ok) return;
      setFeedbackByMessage((prev) => ({ ...prev, [messageId]: rating }));
    };
    const onTranslateResult = (event) => {
      const { messageId, html } = event.detail || {};
      if (!messageId) return;
      setTranslations((prev) => ({ ...prev, [messageId]: html }));
    };
    window.addEventListener(THREAD_EVENTS.STATE, onState);
    window.addEventListener(THREAD_EVENTS.FEEDBACK_RECORDED, onFeedbackRecorded);
    window.addEventListener(THREAD_EVENTS.TRANSLATE_RESULT, onTranslateResult);
    window.dispatchEvent(new CustomEvent("helix-thread-sync"));
    return () => {
      window.removeEventListener(THREAD_EVENTS.STATE, onState);
      window.removeEventListener(THREAD_EVENTS.FEEDBACK_RECORDED, onFeedbackRecorded);
      window.removeEventListener(THREAD_EVENTS.TRANSLATE_RESULT, onTranslateResult);
    };
  }, []);

  // Preserve the anchor on upward merges (加载更早消息); otherwise snap to
  // the newest message — same as the legacy renderer's scrollTop handling.
  useEffect(() => {
    if (!container || !snapshot) return;
    const previousHeight = prevHeightRef.current;
    prevHeightRef.current = container.scrollHeight;
    container.scrollTop = snapshot.preserveAnchor
      ? container.scrollHeight - previousHeight
      : container.scrollHeight;
  }, [container, snapshot]);

  if (!snapshot) return null;

  const handleFeedback = (messageId, rating) => {
    window.dispatchEvent(
      new CustomEvent(THREAD_EVENTS.FEEDBACK, { detail: { messageId, rating } }),
    );
  };
  const handleTranslate = (messageId, language) => {
    window.dispatchEvent(
      new CustomEvent(THREAD_EVENTS.TRANSLATE, { detail: { messageId, language } }),
    );
  };
  const handleLoadOlder = () => {
    window.dispatchEvent(new CustomEvent(THREAD_EVENTS.LOAD_OLDER));
  };

  const messages = Array.isArray(snapshot.messages) ? snapshot.messages : [];
  const noteIds = new Set(messages.filter((m) => m.role === "internal_note").map((m) => m.id));

  if (snapshot.loading) {
    return <div className="thread-empty">正在加载会话</div>;
  }
  if (!messages.length) {
    return <div className="thread-empty">等待第一条客户消息</div>;
  }

  const truncated =
    snapshot.lowPerf && messages.length > 80 ? messages.slice(-80) : messages;

  return (
    <>
      {snapshot.threadPrevCursor ? (
        <div className="thread-load-older">
          <button className="thread-load-older-btn" type="button" onClick={handleLoadOlder}>
            加载更早消息 ↑
          </button>
        </div>
      ) : null}
      {truncated.length < messages.length ? (
        <div className="thread-empty">低配模式仅显示最近 {truncated.length} 条消息</div>
      ) : null}
      {truncated.map((message) => (
        <MessageRow
          key={message.id}
          message={message}
          isReply={message.role === "internal_note" && message.reply_to && noteIds.has(message.reply_to)}
          snapshot={snapshot}
          feedbackRating={feedbackByMessage[message.id]}
          translateHtml={translations[message.id]}
          onFeedback={handleFeedback}
          onTranslate={handleTranslate}
        />
      ))}
    </>
  );
}

export default { mount, ThreadIsland };
