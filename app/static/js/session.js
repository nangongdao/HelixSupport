/**
 * Helix Support — session lifecycle (ROADMAP §41.6 / ARC-001).
 *
 * Mentions inbox and the read-only live watch stream. Extracted from the
 * legacy app.js; app.js keeps thin delegating wrappers with identical
 * names/signatures, so the running UI behaviour is unchanged. app.js calls
 * configure() once at load time with its singletons (state/els/api/…).
 */

let ctx = null;

/** Inject the legacy app.js singletons (state/els/api/helpers). */
export function configure(deps) {
  ctx = deps;
}

export function canReadConversations() {
  return Boolean(ctx.state.me && ctx.state.me.permissions && ctx.state.me.permissions.includes("conversation:read"));
}

export async function loadMentions() {
  // Island mode: the mentions island fetches /api/mentions itself and owns
  // the badge/panel DOM; the legacy controls are yielded (hidden).
  if (window.__HELIX_ISLAND_MODE__) return;
  if (!canReadConversations()) {
    if (ctx.els.mentionsBadge) ctx.els.mentionsBadge.hidden = true;
    return;
  }
  try {
    const payload = await ctx.api("/api/mentions");
    ctx.state.mentions = payload.mentions || [];
    if (ctx.els.mentionsBadge) {
      const count = payload.unread_count || 0;
      ctx.els.mentionsBadge.hidden = count === 0 && !ctx.state.mentionsOpen;
      if (ctx.els.mentionsCount) ctx.els.mentionsCount.textContent = String(count);
      ctx.els.mentionsCount.classList.toggle("is-active", count > 0);
    }
    if (ctx.state.mentionsOpen) renderMentionsPanel(payload.mentions || []);
  } catch {
    // mentions are best-effort in the workspace sidebar
  }
}

export function renderMentionsPanel(mentions) {
  if (!ctx.els.mentionsPanel) return;
  if (!mentions.length) {
    ctx.els.mentionsPanel.innerHTML = '<div class="queue-empty">暂无被提及</div>';
    return;
  }
  const items = mentions
    .map(
      (mention) => `
      <div class="mention-item${mention.unread ? " is-unread" : ""}">
        <div class="mention-top">
          <button class="mention-link" type="button" data-mention-conversation="${ctx.escapeHtml(mention.conversation_id)}">
            <strong>${ctx.escapeHtml(mention.conversation_customer)}</strong>
            <span class="mention-meta">${ctx.escapeHtml(mention.channel)} · ${ctx.escapeHtml(ctx.formatTime(mention.created_at))}${mention.unread ? " · 未读" : ""}</span>
          </button>
          ${mention.unread ? `<button class="mention-read" type="button" data-mention-id="${ctx.escapeHtml(mention.id)}" title="标记已读" aria-label="标记已读">${ctx.icon("check")}</button>` : ""}
        </div>
        <div class="mention-note">${ctx.escapeHtml(mention.note_preview || "（无内容）")}</div>
        <div class="mention-by">— ${ctx.escapeHtml(mention.mentioned_by)}</div>
      </div>`,
    )
    .join("");
  ctx.els.mentionsPanel.innerHTML = `<div class="mentions-heading">我的被提及</div>${items}`;
}

export function toggleMentionsPanel() {
  if (ctx.state.mentionsOpen) {
    closeMentionsPanel();
    return;
  }
  ctx.state.mentionsOpen = true;
  if (ctx.els.mentionsBadge) ctx.els.mentionsBadge.setAttribute("aria-pressed", "true");
  if (ctx.els.mentionsPanel) ctx.els.mentionsPanel.hidden = false;
  loadMentions();
}

export function closeMentionsPanel() {
  ctx.state.mentionsOpen = false;
  if (ctx.els.mentionsBadge) ctx.els.mentionsBadge.setAttribute("aria-pressed", "false");
  if (ctx.els.mentionsPanel) ctx.els.mentionsPanel.hidden = true;
}

export async function markMentionRead(mentionId, button) {
  if (button) button.disabled = true;
  try {
    await ctx.api(`/api/mentions/${encodeURIComponent(mentionId)}/read`, { method: "POST" });
    await loadMentions();
    ctx.showToast("已标记为读");
    // Island mode: tell the island to refetch (loadMentions above is a
    // no-op there); the badge/panel update through the island's query.
    if (window.__HELIX_ISLAND_MODE__) {
      window.dispatchEvent(new CustomEvent("helix-mentions-changed"));
    }
    return true;
  } catch (error) {
    if (button) button.disabled = false;
    ctx.showToast(error.message, true);
    return false;
  }
}

export function startWatching(conversationId) {
  stopWatching();
  if (!conversationId || !canReadConversations()) return;
  if (ctx.els.watchBanner) ctx.els.watchBanner.hidden = false;
  if (ctx.els.watchText) ctx.els.watchText.textContent = "旁观中 · 实时只读";
  const controller = new AbortController();
  ctx.state.watchController = controller;
  void (async () => {
    try {
      const response = await fetch(
        `/api/conversations/${encodeURIComponent(conversationId)}/events?timeout=45`,
        { headers: ctx.baseHeaders, signal: controller.signal },
      );
      if (!response.ok || !response.body) throw new Error(`watch stream failed (${response.status})`);
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const chunks = buffer.split("\n\n");
        buffer = chunks.pop() || "";
        for (const chunk of chunks) {
          let eventName = "message";
          for (const line of chunk.split("\n")) {
            if (line.startsWith("event:")) eventName = line.slice(6).trim();
          }
          if (eventName === "conversation" && !document.hidden) {
            await ctx.loadDetail(conversationId);
          } else if (eventName === "timeout") {
            if (!ctx.state.watchController) return;
          }
        }
      }
    } catch (error) {
      if (error.name === "AbortError") return;
      if (ctx.els.watchBanner) ctx.els.watchBanner.hidden = true;
    } finally {
      if (ctx.state.watchController === controller) ctx.state.watchController = null;
    }
  })();
}

export function stopWatching() {
  if (ctx.state.watchController) {
    ctx.state.watchController.abort();
    ctx.state.watchController = null;
  }
  if (ctx.els.watchBanner) ctx.els.watchBanner.hidden = true;
  if (ctx.els.watchToggle) ctx.els.watchToggle.textContent = "开始旁观";
  if (ctx.els.watchBtn) {
    ctx.els.watchBtn.textContent = "旁观";
    ctx.els.watchBtn.setAttribute("aria-pressed", "false");
  }
}

export function isWatching() {
  return Boolean(ctx.state.watchController);
}

export function toggleWatching() {
  if (isWatching()) {
    stopWatching();
    ctx.showToast("已停止旁观");
    return;
  }
  if (!ctx.state.selectedId) return;
  startWatching(ctx.state.selectedId);
  if (ctx.els.watchText) ctx.els.watchText.textContent = "旁观中 · 实时只读";
  if (ctx.els.watchToggle) ctx.els.watchToggle.textContent = "停止旁观";
  ctx.els.watchBtn.textContent = "旁观中";
  ctx.els.watchBtn.setAttribute("aria-pressed", "true");
  ctx.showToast("旁观模式已开启（只读实时）");
}

/**
 * Bind the mentions inbox and watch-stream DOM (exactly once, at app.js
 * load). Delegates to app.js selectConversation via ctx.
 */
export function bindSession() {
  if (!ctx?.els) return false;
  if (ctx.els.mentionsBadge) {
    ctx.els.mentionsBadge.addEventListener("click", () => {
      toggleMentionsPanel();
      if (!ctx.state.mentionsOpen && ctx.els.mentionsBadge.hidden) loadMentions();
    });
  }
  if (ctx.els.mentionsPanel) {
    ctx.els.mentionsPanel.addEventListener("click", async (event) => {
      const link = event.target.closest("[data-mention-conversation]");
      if (link) {
        const conversationId = link.dataset.mentionConversation;
        closeMentionsPanel();
        if (conversationId) await ctx.selectConversation(conversationId);
        return;
      }
      const readBtn = event.target.closest("[data-mention-id]");
      if (readBtn) {
        await markMentionRead(readBtn.dataset.mentionId, readBtn);
      }
    });
    document.addEventListener("click", (event) => {
      if (ctx.state.mentionsOpen && !ctx.els.mentionsPanel.contains(event.target) && !ctx.els.mentionsBadge.contains(event.target)) {
        closeMentionsPanel();
      }
    });
  }
  if (ctx.els.watchToggle) {
    ctx.els.watchToggle.addEventListener("click", () => {
      toggleWatching();
    });
  }
  if (ctx.els.watchBtn) {
    ctx.els.watchBtn.addEventListener("click", () => {
      toggleWatching();
    });
  }
  return true;
}