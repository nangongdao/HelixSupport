/**
 * Helix Support — attachment surface (ROADMAP §41.6 / ARC-001).
 *
 * Voice/rich-message attachments: pending-per-conversation uploads, chip
 * rendering, name backfill. Extracted from the legacy app.js; app.js keeps
 * thin delegating wrappers with identical names/signatures, so the running
 * UI behaviour is unchanged. app.js calls configure() once at load time.
 */

let ctx = null;

/** Inject the legacy app.js singletons (state/els/api/helpers). */
export function configure(deps) {
  ctx = deps;
}

// pending 附件按会话隔离(conversationId → id[]):上传/移除/发送/清除全部
// 作用在当前会话,避免 A 会话已上传的附件在切到 B 会话发送时被错误挂载。
let pendingAttachmentsByConv = {};
// Backlog: 语音/富媒体消息 — ``attachmentMetaById`` 缓存整个 AttachmentOut
// (filename/content_type/size),供 chip 渲染缩略图与判断图片类型。
let attachmentMetaById = {};
let attachmentNamesLoaded = {};

/** Pending attachment ids for a conversation (read-only; composer reads it). */
export function pendingIds(conversationId) {
  return pendingAttachmentsByConv[conversationId] || [];
}

/** Copy of the known attachment metadata for the thread island snapshot
 * (chips render filenames after loadAttachmentNames resolves). */
export function attachmentMetaSnapshot() {
  const out = {};
  for (const [id, meta] of Object.entries(attachmentMetaById)) {
    out[id] = { filename: meta.filename, content_type: meta.content_type };
  }
  return out;
}

export function attachmentChips(ids) {
  return (ids || [])
    .map((id) => {
      const meta = attachmentMetaById[id] || {};
      const name = meta.filename || id;
      const href = `/api/attachments/${encodeURIComponent(id)}/download`;
      // 图片(安全子集,排除 svg)渲染懒加载缩略图预览;其余走文件下载链接。
      const isImage = /^image\/(png|jpe?g|gif|webp)$/.test(meta.content_type || "");
      return isImage
        ? `<a class="attachment-chip is-image" href="${href}" data-id="${ctx.escapeHtml(id)}" target="_blank" rel="noopener" title="${ctx.escapeHtml(name)} — 点击打开预览">` +
            `<img class="attachment-thumb" src="${href}" alt="" loading="lazy">` +
            `<span class="attachment-chip-name">${ctx.escapeHtml(name)}</span></a>`
        : `<a class="attachment-chip" href="${href}" data-id="${ctx.escapeHtml(id)}" target="_blank" rel="noopener" title="下载 ${ctx.escapeHtml(name)}">${ctx.icon("file-text")}<span class="attachment-chip-name">${ctx.escapeHtml(name)}</span></a>`;
    })
    .join("");
}

export function patchAttachmentChips() {
  document.querySelectorAll(".attachment-chip").forEach((chip) => {
    const id = chip.dataset.id;
    const meta = attachmentMetaById[id];
    if (!meta || !meta.filename) return;
    const label = chip.querySelector(".attachment-chip-name");
    if (label) label.textContent = meta.filename;
  });
}

export async function loadAttachmentNames(conversationId) {
  if (!conversationId || attachmentNamesLoaded[conversationId]) return;
  attachmentNamesLoaded[conversationId] = true;
  try {
    const items = await ctx.api(`/api/attachments?conversation_id=${encodeURIComponent(conversationId)}`);
    for (const item of items || []) attachmentMetaById[item.id] = item;
    patchAttachmentChips();
  } catch {
    /* names stay at the id fallback */
  }
}

export async function uploadPendingAttachment(file) {
  const conversationId = ctx.state.selectedId;
  if (!conversationId || !file) return;
  const form = new FormData();
  form.append("conversation_id", conversationId);
  form.append("file", file, file.name);
  try {
    const attachment = await ctx.api("/api/attachments", {
      method: "POST",
      body: form,
    });
    (pendingAttachmentsByConv[conversationId] = pendingAttachmentsByConv[conversationId] || []).push(attachment.id);
    attachmentMetaById[attachment.id] = attachment;
    renderPendingAttachments();
  } catch (error) {
    ctx.showToast(`附件上传失败：${error.message || error}`, true);
  } finally {
    if (ctx.els.attachmentFile) ctx.els.attachmentFile.value = "";
  }
}

export function renderPendingAttachments() {
  // Island mode: publish the composer state snapshot (the composer island
  // renders the pending chips from it); legacy keeps painting its DOM.
  if (typeof window !== "undefined" && window.__HELIX_ISLAND_MODE__) {
    window.HelixModules?.composerIslandBridge?.publishComposerState?.();
    return;
  }
  if (!ctx.els.pendingAttachments) return;
  const ids = ctx.state.selectedId ? pendingAttachmentsByConv[ctx.state.selectedId] || [] : [];
  if (!ids.length) {
    ctx.els.pendingAttachments.innerHTML = "";
    return;
  }
  ctx.els.pendingAttachments.innerHTML = ids
    .map((id) => `<span class="pending-attachment-chip">${ctx.escapeHtml(attachmentMetaById[id]?.filename || id)} <button type="button" class="pending-attachment-remove" data-id="${ctx.escapeHtml(id)}" title="移除">×</button></span>`)
    .join("");
}

export function clearPendingAttachments(conversationId) {
  const key = conversationId || ctx.state.selectedId;
  if (key) delete pendingAttachmentsByConv[key];
  renderPendingAttachments();
}

export function renderAttachmentBar(detail) {
  const conversation = detail.conversation;
  const human = ["waiting_human", "human_active"].includes(conversation.status);
  const visible = human && ctx.canOperate();
  if (typeof window !== "undefined" && window.__HELIX_ISLAND_MODE__) {
    // The island derives the bar visibility from the composer state snapshot.
    if (!visible) delete pendingAttachmentsByConv[conversation.id];
    renderPendingAttachments();
    return;
  }
  if (!ctx.els.attachmentBar) return;
  ctx.els.attachmentBar.hidden = !visible;
  if (ctx.els.attachmentBar.hidden) delete pendingAttachmentsByConv[conversation.id];
  renderPendingAttachments();
}

/** Shared pending-attachment removal (legacy chip click + island bridge). */
export function removePendingAttachment(conversationId, id) {
  if (!conversationId || !id) return;
  pendingAttachmentsByConv[conversationId] = (
    pendingAttachmentsByConv[conversationId] || []
  ).filter((existing) => existing !== id);
  renderPendingAttachments();
}

/**
 * Bind the attachment upload/chip DOM (exactly once, at app.js load).
 */
export function bindAttachments() {
  if (!ctx?.els) return false;
  if (ctx.els.attachmentFile) {
    ctx.els.attachmentFile.addEventListener("change", () => {
      if (ctx.els.attachmentFile.files?.length) void uploadPendingAttachment(ctx.els.attachmentFile.files[0]);
    });
  }
  if (ctx.els.pendingAttachments) {
    ctx.els.pendingAttachments.addEventListener("click", (event) => {
      const button = event.target.closest(".pending-attachment-remove");
      if (!button) return;
      removePendingAttachment(ctx.state.selectedId, button.dataset.id);
    });
  }
  return true;
}