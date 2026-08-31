/**
 * Helix Support — conversation detail lifecycle (app.js <500 campaign).
 *
 * The operator's conversation-detail assembly, extracted verbatim from app.js:
 * loadDetail fetches the transcript tail (+ opaque X-Prev-Cursor) and hands it
 * to renderDetail; selectConversation switches the operator onto a
 * conversation (nudge/load/stop-watch/close-drawer); ensureSelectedRowVisible
 * keeps the virtualized row in the viewport; clearSelection resets the detail
 * view; renderSubtitle/renderLanguagePicker paint the header readout.
 *
 * All of these arrive their app.js-scoped dependencies through configure —
 * there are no imports to avoid the classic-script hoisting pitfalls.
 */

let ctx = null;

/** Inject the legacy app.js singletons (state/els/apiWithHeaders/loaders). */
function configure(deps) {
  ctx = deps;
}

function renderSubtitle(conversation) {
  const languageName = conversation.language
    ? ctx.languageNames[conversation.language] || conversation.language
    : "";
  const subtitleParts = [
    conversation.id,
    conversation.channel,
    conversation.customer_ref || "未绑定身份",
  ];
  if (languageName) subtitleParts.push(`语言:${languageName}`);
  ctx.els.conversationSubtitle.textContent = subtitleParts.join(" · ");
}

// Backlog (多语言客服): the header select mirrors the stored manual override
// (empty = auto). Options are injected once; renderDetail sets the value so a
// PATCH error can roll back by re-rendering.
function renderLanguagePicker(conversation) {
  if (!ctx.els.conversationLanguageSelect) return;
  // viewer/auditor have no conversation:write — hide the override control so
  // read-only roles don't get a control that would always 403.
  ctx.els.conversationLanguageSelect.hidden = !ctx.canWriteConversations();
  if (!ctx.els.conversationLanguageSelect.dataset.built) {
    ctx.els.conversationLanguageSelect.insertAdjacentHTML("beforeend", ctx.languageOptions);
    ctx.els.conversationLanguageSelect.dataset.built = "1";
  }
  ctx.els.conversationLanguageSelect.value = conversation.language || "";
}

async function loadDetail(id) {
  const sequence = ++ctx.state.detailSequence;
  // ROADMAP §18.4: always load the newest tail of the transcript, then lazily
  // fetch older messages upward. The detail response echoes an opaque
  // X-Prev-Cursor header (docs/API_POLICY.md §3 — clients never parse cursors).
  const limit = ctx.state.lowPerf ? 80 : ctx.threadPageLimit;
  const { response, data: detail } = await ctx.apiWithHeaders(
    `/api/conversations/${encodeURIComponent(id)}?message_limit=${limit}&messages_before=true`,
  );
  if (ctx.state.selectedId !== id || sequence !== ctx.state.detailSequence) return false;
  ctx.state.threadPrevCursor = response.headers.get("X-Prev-Cursor") || null;
  ctx.state.threadLoadingOlder = false;
  ctx.renderDetail(detail);
  return true;
}

async function selectConversation(id) {
  ctx.state.selectedId = id;
  // 切换会话时收起 note 的候选列表(旧会话的 @token 不再适用)。
  ctx.hideMentionSuggest();
  // Nudge first so a virtual-mode re-render centers on the just-selected row.
  ensureSelectedRowVisible(id);
  ctx.renderQueue();
  ctx.els.emptyState.hidden = true;
  ctx.els.conversationView.hidden = false;
  // Island mode: the thread island renders the loading state from a
  // helix-thread-state snapshot; the legacy container is hidden.
  if (typeof window !== "undefined" && window.__HELIX_ISLAND_MODE__) {
    window.HelixModules?.thread?.publishThreadState?.({ loading: true });
  } else {
    ctx.els.messages.innerHTML = '<div class="thread-empty">正在加载会话</div>';
  }
  ctx.stopWatching();
  try {
    await loadDetail(id);
    if (ctx.els.queuePane.classList.contains("is-open")) ctx.closeQueueDrawer({ restoreFocus: false });
  } catch (error) {
    ctx.showToast(error.message, true);
    clearSelection();
  }
}

// ROADMAP §18.4: in virtual mode the selected row may sit outside the rendered
// window — nudge the scrollport so it lands inside the viewport band.
function ensureSelectedRowVisible(id) {
  if (!ctx.state.queueVirtual) return;
  const index = ctx.state.conversations.findIndex((c) => c.id === id);
  if (index < 0) return;
  const rowHeight = ctx.windowedRowHeight();
  const top = index * rowHeight;
  const bottom = top + rowHeight;
  if (top < ctx.els.list.scrollTop) ctx.els.list.scrollTop = top;
  else if (bottom > ctx.els.list.scrollTop + ctx.els.list.clientHeight) {
    ctx.els.list.scrollTop = bottom - ctx.els.list.clientHeight;
  }
}

function clearSelection() {
  ctx.stopWatching();
  ctx.state.detailSequence += 1;
  ctx.state.selectedId = null;
  ctx.state.detail = null;
  ctx.els.emptyState.hidden = false;
  ctx.els.conversationView.hidden = true;
  ctx.els.noteForm.hidden = true;
  ctx.resetCopilot();
  ctx.renderQueue();
}

export {
  configure,
  renderSubtitle,
  renderLanguagePicker,
  loadDetail,
  selectConversation,
  ensureSelectedRowVisible,
  clearSelection,
};

export default {
  configure,
  renderSubtitle,
  renderLanguagePicker,
  loadDetail,
  selectConversation,
  ensureSelectedRowVisible,
  clearSelection,
};