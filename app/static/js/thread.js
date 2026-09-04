/**
 * Helix Support — message thread domain module (D3 long tail glue slice).
 *
 * The conversation transcript: bubble rendering, upward pagination
 * (ROADMAP §18.4 keyset cursors), per-message feedback and translation
 * flows. Extracted from the legacy app.js; app.js keeps thin delegating
 * wrappers with identical names/signatures, so the running UI behaviour is
 * unchanged. app.js calls configure() once at load time.
 *
 * Island mode: the transcript is island-rendered but legacy-fed — this
 * module keeps the loadDetail/loadOlderMessages lifecycle and the
 * feedback/translate write paths, publishes the thread snapshot via
 * helix-thread-state, and the thread island bridges interactions back:
 *   helix-thread-load-older  {}                     → loadOlderMessages
 *   helix-thread-feedback    {messageId, rating}    → recordFeedback
 *   helix-thread-translate   {messageId, language}  → requestTranslation
 * and receives helix-thread-feedback-recorded / -translate-result
 * completions so the island mirrors the legacy DOM state changes.
 */

let ctx = null;

/** Inject the legacy app.js singletons (state/els/api/helpers). */
export function configure(deps) {
  ctx = deps;
}

/** Events shared with the thread island (mirrored in thread-island.jsx). */
export const THREAD_EVENTS = Object.freeze({
  STATE: "helix-thread-state",
  LOAD_OLDER: "helix-thread-load-older",
  FEEDBACK: "helix-thread-feedback",
  FEEDBACK_RECORDED: "helix-thread-feedback-recorded",
  TRANSLATE: "helix-thread-translate",
  TRANSLATE_RESULT: "helix-thread-translate-result",
});

/** Role display names for the message meta line (legacy parity). */
export const ROLE_NAMES = Object.freeze({
  customer: "客户",
  assistant: "自动客服",
  operator: "人工客服",
  internal_note: "内部备注",
});

/** Build the island snapshot from current state. Pure given ctx. */
export function currentThreadSnapshot({ messages, preserveAnchor = false, loading = false } = {}) {
  const attachmentMeta =
    typeof window !== "undefined"
      ? window.HelixModules?.attachments?.attachmentMetaSnapshot?.() || {}
      : {};
  return {
    loading: Boolean(loading),
    messages: loading ? [] : messages ?? ctx.state.detail?.messages ?? [],
    threadPrevCursor: loading ? null : ctx.state.threadPrevCursor || null,
    lowPerf: Boolean(ctx.state.lowPerf),
    canTranslate: Boolean(ctx.canWriteConversations?.()),
    preserveAnchor: Boolean(preserveAnchor),
    languages: ctx.languageNames
      ? Object.keys(ctx.languageNames)
          .sort((a, b) => ctx.languageNames[a].localeCompare(ctx.languageNames[b], "zh"))
          .map((code) => ({ code, name: ctx.languageNames[code] }))
      : [],
    attachmentMeta,
  };
}

/** Publish the thread snapshot to the island (no-op outside island mode). */
export function publishThreadState(overrides = {}) {
  if (typeof window === "undefined" || !window.__HELIX_ISLAND_MODE__) return false;
  window.dispatchEvent(
    new CustomEvent(THREAD_EVENTS.STATE, { detail: currentThreadSnapshot(overrides) }),
  );
  return true;
}

export function renderMessages(messages, { preserveAnchor = false } = {}) {
  // Island mode: the thread island owns #messages (yieldsLegacy) — publish
  // the snapshot and keep the scroll anchoring decision with it.
  if (typeof window !== "undefined" && window.__HELIX_ISLAND_MODE__) {
    publishThreadState({ messages, preserveAnchor });
    return;
  }
  const oldThreadScrollHeight = preserveAnchor ? ctx.els.messages.scrollHeight : 0;
  if (!messages.length) {
    ctx.els.messages.innerHTML = '<div class="thread-empty">等待第一条客户消息</div>';
    return;
  }
  const visibleMessages = ctx.state.lowPerf && messages.length > 80 ? messages.slice(-80) : messages;
  const noteIds = new Set(visibleMessages.filter((m) => m.role === "internal_note").map((m) => m.id));
  const parts = new Array(visibleMessages.length);
  for (let index = 0; index < visibleMessages.length; index += 1) {
    const message = visibleMessages[index];
    const metadata = message.metadata || {};
    const agent = message.role === "assistant" && metadata.agent
      ? `<span class="agent-chip">${ctx.escapeHtml(metadata.agent)}</span>`
      : "";
    const feedback = message.role === "assistant"
      ? `<div class="feedback-actions" aria-label="回答反馈">
            <button class="feedback-button" type="button" data-feedback="1" data-message-id="${ctx.escapeHtml(message.id)}" title="有帮助" aria-label="有帮助" aria-pressed="false">${ctx.icon("thumbs-up")}</button>
            <button class="feedback-button" type="button" data-feedback="-1" data-message-id="${ctx.escapeHtml(message.id)}" title="需改进" aria-label="需改进" aria-pressed="false">${ctx.icon("thumbs-down")}</button>
          </div>`
      : "";
    // Backlog (多语言客服): customer messages carry a per-message translate
    // bar. Without a configured provider the backend echoes the original text
    // with was_translated=false and the result line degrades to a notice.
    const translateBar = message.role === "customer" && ctx.canWriteConversations()
      ? `<div class="translate-bar" data-message-id="${ctx.escapeHtml(message.id)}">
            <select class="translate-lang" aria-label="翻译目标语言">${ctx.languageOptions}</select>
            <button class="translate-button" type="button">翻译</button>
            <span class="translate-result"></span>
          </div>`
      : "";
    const isReply = message.role === "internal_note" && message.reply_to && noteIds.has(message.reply_to);
    const replyMark = isReply
      ? `<span class="note-reply-mark" title="回复了 ${ctx.escapeHtml(message.reply_to)}">↳ 回复</span>`
      : "";
    const contentHtml = ctx.escapeHtml(message.content).replace(
      /@([A-Za-z0-9._:@/-]{2,64})/g,
      '<span class="mention-chip">@$1</span>',
    );
    const attachments = metadata.attachment_ids?.length
      ? `<div class="message-attachments">${ctx.attachmentChips(metadata.attachment_ids)}</div>`
      : "";
    parts[index] = `
        <div class="message-row ${ctx.escapeHtml(message.role)}${isReply ? " is-reply" : ""}">
          <div class="message-card">
            <div class="message-meta">
              <span>${ctx.escapeHtml(ROLE_NAMES[message.role] || message.role)}</span>
              ${agent}
              ${replyMark}
              <time datetime="${ctx.escapeHtml(message.created_at)}">${ctx.escapeHtml(ctx.formatTime(message.created_at))}</time>
            </div>
            <div class="message-bubble">${contentHtml}</div>
            ${attachments}
            ${feedback}
            ${translateBar}
          </div>
        </div>`;
  }
  const truncation = visibleMessages.length < messages.length
    ? `<div class="thread-empty">低配模式仅显示最近 ${visibleMessages.length} 条消息</div>`
    : "";
  // ROADMAP §18.4: a thread with a live X-Prev-Cursor header can grow upward —
  // the affordance doubles as the scroll-to-top trigger anchor.
  const loadOlder = ctx.state.threadPrevCursor
    ? '<div class="thread-load-older"><button class="thread-load-older-btn" type="button">加载更早消息 ↑</button></div>'
    : "";
  ctx.els.messages.innerHTML = loadOlder + truncation + parts.join("");
  ctx.els.messages.querySelector(".thread-load-older-btn")?.addEventListener("click", loadOlderMessages);
  // Preserving the anchor matters for the upward merge: keep the view where it
  // was instead of snapping to the newest message after prepending older ones.
  ctx.els.messages.scrollTop = preserveAnchor
    ? ctx.els.messages.scrollHeight - oldThreadScrollHeight
    : ctx.els.messages.scrollHeight;
}

export async function loadOlderMessages() {
  if (!ctx.state.selectedId || ctx.state.threadLoadingOlder || !ctx.state.threadPrevCursor) return;
  ctx.state.threadLoadingOlder = true;
  const cursor = ctx.state.threadPrevCursor;
  try {
    const { response, data } = await ctx.apiWithHeaders(
      `/api/conversations/${encodeURIComponent(ctx.state.selectedId)}/messages?limit=${ctx.olderPageSize}&before=true&cursor=${encodeURIComponent(cursor)}`,
    );
    const older = Array.isArray(data) ? data : [];
    const merged = [...older, ...(ctx.state.detail?.messages || [])];
    // X-Has-More is the authoritative "older messages exist" signal: a partial
    // page (fewer than requested) means we have reached the top.
    const hasMore = response.headers.get("X-Has-More") === "true";
    if (older.length) {
      ctx.state.threadPrevCursor = hasMore ? response.headers.get("X-Prev-Cursor") || null : null;
      ctx.state.detail = { ...(ctx.state.detail || {}), messages: merged };
    } else {
      ctx.state.threadPrevCursor = null; // nothing older — drop the affordance
    }
    renderMessages(merged, { preserveAnchor: true });
  } catch (error) {
    ctx.showToast(error.message || "加载更早消息失败");
  } finally {
    ctx.state.threadLoadingOlder = false;
  }
}

/** Shared feedback write (legacy button flow + island bridge). Silent —
 * callers own the UI acknowledgement. */
export async function recordFeedback({ messageId, rating }) {
  if (!ctx.state.selectedId) throw new Error("未选择会话");
  await ctx.api(`/api/conversations/${encodeURIComponent(ctx.state.selectedId)}/feedback`, {
    method: "POST",
    body: JSON.stringify({
      message_id: messageId,
      rating: Number(rating),
    }),
  });
}

export async function submitFeedback(button) {
  if (!ctx.state.selectedId) return;
  button.disabled = true;
  try {
    await recordFeedback({
      messageId: button.dataset.messageId,
      rating: Number(button.dataset.feedback),
    });
    button.classList.add("is-recorded");
    button.setAttribute("aria-pressed", "true");
    button.title = "已记录";
    ctx.showToast("反馈已记录");
  } catch (error) {
    button.disabled = false;
    ctx.showToast(error.message, true);
  }
}

/** Shared translation write (legacy button flow + island bridge). Silent —
 * callers own the UI acknowledgement. */
export async function requestTranslation({ messageId, language }) {
  if (!ctx.state.selectedId) throw new Error("未选择会话");
  return ctx.api(
    `/api/conversations/${encodeURIComponent(ctx.state.selectedId)}/messages/${encodeURIComponent(messageId)}/translate`,
    {
      method: "POST",
      body: JSON.stringify({ target_language: language }),
    },
  );
}

/** Build the inline translate result markup (shared by both flows).
 * was_translated=false (no provider / target == service language) degrades to
 * a notice instead of a misleading "translation". */
export function buildTranslateResultHtml(out, language) {
  if (out.was_translated) {
    return (
      `<span class="translate-text">${ctx.escapeHtml(out.translated)}</span>` +
      `<span class="translate-tag">已翻译为 ${ctx.escapeHtml(ctx.languageNames[language] || language)}</span>`
    );
  }
  if (out.source === "none") {
    // Backlog (多语言客服): target == service language — nothing to translate,
    // don't blame a missing model.
    return "目标语言与当前服务语言一致，无需翻译";
  }
  return "当前无翻译模型，已返回原文";
}

export async function translateMessage(button) {
  if (!ctx.state.selectedId) return;
  const bar = button.closest(".translate-bar");
  if (!bar) return;
  const language = bar.querySelector(".translate-lang").value;
  const result = bar.querySelector(".translate-result");
  button.disabled = true;
  result.textContent = "";
  try {
    const out = await requestTranslation({ messageId: bar.dataset.messageId, language });
    result.innerHTML = buildTranslateResultHtml(out, language);
  } catch (error) {
    result.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

/**
 * Bind the thread interactions (exactly once, at app.js load): the legacy
 * #messages click routing plus the island bridges. In a plain browser tab
 * the island events never fire; in island mode the legacy #messages is
 * hidden so its click handler never fires.
 */
export function bindThread() {
  if (!ctx?.els?.messages) return false;
  ctx.els.messages.addEventListener("click", (event) => {
    const path = typeof event.composedPath === "function" ? event.composedPath() : [];
    const button =
      path.find((node) => node instanceof HTMLElement && node.classList?.contains("feedback-button")) ||
      event.target.closest?.(".feedback-button");
    if (button) submitFeedback(button);
    const translateButton =
      path.find((node) => node instanceof HTMLElement && node.classList?.contains("translate-button")) ||
      event.target.closest?.(".translate-button");
    if (translateButton) void translateMessage(translateButton);
  });
  if (typeof window !== "undefined") {
    window.addEventListener("helix-thread-sync", () => publishThreadState());
    window.addEventListener(THREAD_EVENTS.LOAD_OLDER, () => {
      void loadOlderMessages();
    });
    window.addEventListener(THREAD_EVENTS.FEEDBACK, (event) => {
      const { messageId, rating } = event.detail || {};
      if (!messageId || !rating) return;
      void (async () => {
        let ok = true;
        try {
          await recordFeedback({ messageId, rating });
          ctx.showToast("反馈已记录");
        } catch (error) {
          ok = false;
          ctx.showToast(error.message || "反馈提交失败", true);
        }
        window.dispatchEvent(
          new CustomEvent(THREAD_EVENTS.FEEDBACK_RECORDED, { detail: { messageId, rating, ok } }),
        );
      })();
    });
    window.addEventListener(THREAD_EVENTS.TRANSLATE, (event) => {
      const { messageId, language } = event.detail || {};
      if (!messageId || !language) return;
      void (async () => {
        let html = "";
        try {
          const out = await requestTranslation({ messageId, language });
          html = buildTranslateResultHtml(out, language);
        } catch (error) {
          html = ctx.escapeHtml(error.message || "翻译失败");
        }
        window.dispatchEvent(
          new CustomEvent(THREAD_EVENTS.TRANSLATE_RESULT, { detail: { messageId, html } }),
        );
      })();
    });
  }
  return true;
}

export default {
  THREAD_EVENTS,
  ROLE_NAMES,
  currentThreadSnapshot,
  publishThreadState,
  renderMessages,
  loadOlderMessages,
  recordFeedback,
  submitFeedback,
  requestTranslation,
  buildTranslateResultHtml,
  translateMessage,
  bindThread,
};
