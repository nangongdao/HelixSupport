/**
 * Helix Support — knowledge view lifecycle (D3 long tail slice 17).
 *
 * The knowledge page's DOM lifecycle (summary/filter/list, cached fetch,
 * editor + review flows, listeners and island bridges); app.js keeps a thin
 * loadKnowledgeView wrapper. Browser tab → legacy paint; desktop shell → the
 * knowledge island owns the surface, writes stay here.
 */

import {
  KNOWLEDGE_STATUS_LABELS, knowledgeFormPayload, normalizeKnowledgeArticle,
  filterKnowledgeArticles, summarizeKnowledgeArticles, reviewActionsFor,
} from "./knowledge.js?v=1.4.0";

let ctx = null;

/** Inject the legacy app.js singletons (state/els/api/helpers). */
export function configure(deps) {
  ctx = deps;
}

export const KNOWLEDGE_EVENTS = Object.freeze({
  REFRESH: "helix-knowledge-refresh", NEW: "helix-knowledge-new", SAVE: "helix-knowledge-save",
  SAVED: "helix-knowledge-saved", ACTION: "helix-knowledge-action",
});

export function canWriteKnowledge() {
  return ctx.state.me?.permissions?.includes("knowledge:write") === true;
}
export function syncKnowledgeLanguageSelects() {
  // Selects must cover every languageNames entry or unlisted-language
  // articles silently lose their language on edit (audit: backlog 1).
  for (const select of [ctx.els.knowledgeLanguage, ctx.els.knowledgeLanguageFilter]) {
    if (!select) continue;
    const covered = new Set([...select.options].map((option) => option.value));
    const missing = Object.keys(ctx.languageNames)
      .sort((a, b) => ctx.languageNames[a].localeCompare(ctx.languageNames[b], "zh"))
      .filter((code) => !covered.has(code));
    for (const code of missing) {
      select.insertAdjacentHTML("beforeend", `<option value="${code}">${ctx.languageNames[code]}</option>`);
    }
  }
}

export function knowledgeFilters() {
  return {
    query: ctx.els.knowledgeSearch?.value || "",
    status: ctx.els.knowledgeStatusFilter?.value || "all",
    language: ctx.els.knowledgeLanguageFilter?.value || "",
  };
}

export function renderKnowledgeSummary() {
  if (!ctx.els.knowledgeSummary) return;
  const summary = summarizeKnowledgeArticles(ctx.state.knowledgeArticles);
  const writer = canWriteKnowledge();
  const rows = [
    ["全部", summary.total],
    ["已发布", summary.published],
    ["草稿", writer ? summary.draft : "—"],
    ["待审核", writer ? summary.pending_review : "—"],
    ["已停用", writer ? summary.retired : "—"],
  ];
  ctx.els.knowledgeSummary.innerHTML = rows
    .map(
      ([label, count]) =>
        `<div class="knowledge-summary-item"><span>${label}</span><strong>${ctx.escapeHtml(count)}</strong></div>`,
    )
    .join("");
}

export function knowledgeSourceMarkup(article) {
  const source = String(article.source_url || "");
  if (/^https?:\/\//i.test(source)) {
    return `<a class="knowledge-source" href="${ctx.escapeHtml(source)}" target="_blank" rel="noopener noreferrer" title="${ctx.escapeHtml(source)}">${ctx.escapeHtml(source)}</a>`;
  }
  return `<span class="knowledge-source" title="${ctx.escapeHtml(source)}">${ctx.escapeHtml(source || "未记录来源")}</span>`;
}

export function renderKnowledgeArticles() {
  if (!ctx.els.knowledgeList) return;
  const articles = filterKnowledgeArticles(ctx.state.knowledgeArticles, knowledgeFilters());
  const writer = canWriteKnowledge();
  if (ctx.els.knowledgeResultCount) {
    ctx.els.knowledgeResultCount.textContent = `${articles.length} / ${ctx.state.knowledgeArticles.length} 篇`;
  }
  if (!articles.length) {
    ctx.els.knowledgeList.innerHTML = "";
    if (ctx.els.knowledgeListStatus) {
      ctx.els.knowledgeListStatus.textContent = ctx.state.knowledgeArticles.length
        ? "没有符合当前筛选条件的文章。"
        : "当前租户还没有知识文章。";
    }
    return;
  }
  if (ctx.els.knowledgeListStatus) ctx.els.knowledgeListStatus.textContent = "";
  ctx.els.knowledgeList.innerHTML = articles
    .map((raw) => {
      const article = normalizeKnowledgeArticle(raw);
      const status = Object.hasOwn(KNOWLEDGE_STATUS_LABELS, article.status)
        ? article.status
        : "retired";
      const statusLabel = KNOWLEDGE_STATUS_LABELS[status] || status;
      const language = article.language ? ctx.languageNames[article.language] || article.language : "通用";
      const tags = article.tags.length
        ? article.tags.map((tag) => `<span class="knowledge-tag">${ctx.escapeHtml(tag)}</span>`).join("")
        : '<span class="knowledge-tag">未分类</span>';
      const reviewButtons = writer
        ? reviewActionsFor(article)
            .map((action) => {
              const label = action === "publish" ? "发布" : "停用";
              return `<button type="button" class="knowledge-action" data-action="${action}" data-article-id="${ctx.escapeHtml(article.id)}">${label}</button>`;
            })
            .join("")
        : "";
      const editButton = writer
        ? `<button type="button" class="knowledge-action" data-action="edit" data-article-id="${ctx.escapeHtml(article.id)}">编辑</button>`
        : "";
      return `<article class="knowledge-article" role="listitem" data-article-id="${ctx.escapeHtml(article.id)}">
        <div class="knowledge-article-head">
          <h3>${ctx.escapeHtml(article.title)}</h3>
          <span class="knowledge-status ${status}">${ctx.escapeHtml(statusLabel)}</span>
        </div>
        <div class="knowledge-article-meta">
          <span>${ctx.escapeHtml(article.category)}</span><span>·</span>
          <span>${ctx.escapeHtml(language)}</span><span>·</span>
          <span>v${ctx.escapeHtml(article.version || 1)}</span><span>·</span>
          <time>${ctx.escapeHtml(ctx.formatTime(article.updated_at, true))}</time>
        </div>
        <div class="knowledge-tag-list">${tags}</div>
        <details>
          <summary>查看正文</summary>
          <p class="knowledge-article-content">${ctx.escapeHtml(article.content)}</p>
        </details>
        ${knowledgeSourceMarkup(article)}
        ${writer ? `<div class="knowledge-article-actions">${editButton}${reviewButtons}</div>` : ""}
      </article>`;
    })
    .join("");
}

export function updateKnowledgeAccessState() {
  const writer = canWriteKnowledge();
  if (ctx.els.newKnowledgeDraft) ctx.els.newKnowledgeDraft.hidden = !writer;
  // Island mode: the island owns the notice/filter/editor (no un-hiding).
  if (typeof window !== "undefined" && window.__HELIX_ISLAND_MODE__) return;
  if (ctx.els.knowledgeReadOnly) ctx.els.knowledgeReadOnly.hidden = writer;
  if (!writer && ctx.els.knowledgeEditor) ctx.els.knowledgeEditor.hidden = true;
  if (!writer && ctx.els.knowledgeStatusFilter) {
    for (const option of ctx.els.knowledgeStatusFilter.options) {
      option.disabled = option.value !== "all" && option.value !== "published";
    }
    if (!["all", "published"].includes(ctx.els.knowledgeStatusFilter.value)) {
      ctx.els.knowledgeStatusFilter.value = "published";
    }
  } else if (writer && ctx.els.knowledgeStatusFilter) {
    for (const option of ctx.els.knowledgeStatusFilter.options) option.disabled = false;
  }
}

export async function loadKnowledgeView({ force = false } = {}) {
  if (!ctx.els.knowledgeView) return;
  updateKnowledgeAccessState();
  // Island mode: the island owns the fetch + DOM — hand the refresh over
  // (`force` carries the cache decision for a fresh react-query fetch).
  if (typeof window !== "undefined" && window.__HELIX_ISLAND_MODE__) {
    window.dispatchEvent(new CustomEvent(KNOWLEDGE_EVENTS.REFRESH, { detail: { force } }));
    return;
  }
  syncKnowledgeLanguageSelects();
  // Per-permission cache: role changes never leak drafts to readers (backlog 4).
  if (
    !force &&
    ctx.state.knowledgeLoadedForWriter === canWriteKnowledge() &&
    ctx.state.knowledgeLoadedAt &&
    Date.now() - ctx.state.knowledgeLoadedAt < 15000
  ) {
    renderKnowledgeSummary();
    renderKnowledgeArticles();
    return;
  }
  if (ctx.els.knowledgeList) ctx.els.knowledgeList.setAttribute("aria-busy", "true");
  if (ctx.els.knowledgeListStatus) ctx.els.knowledgeListStatus.textContent = "正在加载文章…";
  try {
    const path = canWriteKnowledge() ? "/api/knowledge?include_inactive=true" : "/api/knowledge";
    const articles = await ctx.api(path);
    ctx.state.knowledgeArticles = Array.isArray(articles)
      ? articles.map(normalizeKnowledgeArticle)
      : [];
    ctx.state.knowledgeLoadedForWriter = canWriteKnowledge();
    ctx.state.knowledgeLoadedAt = Date.now();
    renderKnowledgeSummary();
    renderKnowledgeArticles();
  } catch (error) {
    ctx.state.knowledgeArticles = [];
    ctx.state.knowledgeLoadedForWriter = null;
    ctx.state.knowledgeLoadedAt = 0;
    if (ctx.els.knowledgeList) ctx.els.knowledgeList.innerHTML = "";
    if (ctx.els.knowledgeListStatus) {
      ctx.els.knowledgeListStatus.textContent = `知识文章加载失败：${error.message || error}`;
    }
  } finally {
    if (ctx.els.knowledgeList) ctx.els.knowledgeList.setAttribute("aria-busy", "false");
  }
}

export function resetKnowledgeEditor({ close = false } = {}) {
  ctx.state.knowledgeEditingId = null;
  // eslint-disable-next-line no-multi-assign
  ctx.els.knowledgeForm?.reset();
  if (ctx.els.knowledgeCategory) ctx.els.knowledgeCategory.value = "general";
  if (ctx.els.knowledgeEditorTitle) ctx.els.knowledgeEditorTitle.textContent = "新建知识草稿";
  if (ctx.els.knowledgeSaveLabel) ctx.els.knowledgeSaveLabel.textContent = "保存草稿";
  if (ctx.els.knowledgeEditor) ctx.els.knowledgeEditor.hidden = close;
}

export function editKnowledgeArticle(articleId) {
  if (!canWriteKnowledge()) return;
  const raw = ctx.state.knowledgeArticles.find((article) => article.id === articleId);
  if (!raw) return;
  const article = normalizeKnowledgeArticle(raw);
  ctx.state.knowledgeEditingId = article.id;
  if (ctx.els.knowledgeTitle) ctx.els.knowledgeTitle.value = article.title;
  if (ctx.els.knowledgeContent) ctx.els.knowledgeContent.value = article.content;
  if (ctx.els.knowledgeTags) ctx.els.knowledgeTags.value = article.tags.join(", ");
  if (ctx.els.knowledgeCategory) ctx.els.knowledgeCategory.value = article.category;
  if (ctx.els.knowledgeLanguage) ctx.els.knowledgeLanguage.value = article.language || "";
  if (ctx.els.knowledgeSource) ctx.els.knowledgeSource.value = article.source_url;
  if (ctx.els.knowledgeEditorTitle) ctx.els.knowledgeEditorTitle.textContent = "编辑知识文章";
  if (ctx.els.knowledgeSaveLabel) ctx.els.knowledgeSaveLabel.textContent = "保存修改";
  if (ctx.els.knowledgeEditor) ctx.els.knowledgeEditor.hidden = false;
  ctx.els.knowledgeTitle?.focus({ preventScroll: true });
}

export async function saveKnowledgeArticle(event) {
  event.preventDefault();
  if (!canWriteKnowledge() || !ctx.els.knowledgeForm) return;
  const payload = knowledgeFormPayload({
    title: ctx.els.knowledgeTitle?.value,
    content: ctx.els.knowledgeContent?.value,
    tags: ctx.els.knowledgeTags?.value,
    category: ctx.els.knowledgeCategory?.value,
    sourceUrl: ctx.els.knowledgeSource?.value,
    language: ctx.els.knowledgeLanguage?.value,
  });
  if (!payload.tags.length) {
    ctx.showToast("请至少填写一个知识标签", true);
    ctx.els.knowledgeTags?.focus();
    return;
  }
  // minlength counts raw chars; a trimmed payload can still miss the backend
  // minimums (2 title / 10 content) — surface it before the 422.
  if (payload.title.length < 2) {
    ctx.showToast("标题至少需要 2 个字符", true);
    ctx.els.knowledgeTitle?.focus();
    return;
  }
  if (payload.content.length < 10) {
    ctx.showToast("正文至少需要 10 个字符", true);
    ctx.els.knowledgeContent?.focus();
    return;
  }
  const editingId = ctx.state.knowledgeEditingId;
  ctx.setFormBusy(ctx.els.knowledgeForm, true);
  try {
    const path = editingId
      ? `/api/knowledge/${encodeURIComponent(editingId)}`
      : "/api/knowledge/drafts";
    await ctx.api(path, {
      method: editingId ? "PATCH" : "POST",
      body: JSON.stringify(payload),
    });
    resetKnowledgeEditor({ close: true });
    ctx.state.knowledgeLoadedAt = 0;
    await loadKnowledgeView({ force: true });
    ctx.showToast(editingId ? "知识文章已更新" : "知识草稿已创建");
  } catch (error) {
    ctx.showToast(`知识文章保存失败：${error.message || error}`, true);
  } finally {
    ctx.setFormBusy(ctx.els.knowledgeForm, false);
  }
}

// Bridge: the island validates before dispatching; this is the api()/toast half.
export async function saveKnowledgeFromIsland({ payload, editingId } = {}) {
  if (!canWriteKnowledge() || !payload) return;
  let ok = false;
  try {
    const path = editingId
      ? `/api/knowledge/${encodeURIComponent(editingId)}`
      : "/api/knowledge/drafts";
    await ctx.api(path, { method: editingId ? "PATCH" : "POST", body: JSON.stringify(payload) });
    ok = true;
    ctx.state.knowledgeLoadedAt = 0;
    ctx.showToast(editingId ? "知识文章已更新" : "知识草稿已创建");
  } catch (error) {
    ctx.showToast(`知识文章保存失败：${error.message || error}`, true);
  } finally {
    window.dispatchEvent(new CustomEvent(KNOWLEDGE_EVENTS.SAVED, { detail: { ok } }));
  }
}

export async function reviewKnowledgeArticle(articleId, action) {
  if (!canWriteKnowledge() || !["publish", "retire"].includes(action)) return;
  if (action === "retire" && !window.confirm("确认停用该知识文章？停用后将不再参与检索。")) {
    return;
  }
  if (ctx.els.knowledgeList) ctx.els.knowledgeList.setAttribute("aria-busy", "true");
  try {
    await ctx.api(`/api/knowledge/${encodeURIComponent(articleId)}/review`, {
      method: "POST",
      body: JSON.stringify({ action }),
    });
    ctx.state.knowledgeLoadedAt = 0;
    await loadKnowledgeView({ force: true });
    ctx.showToast(action === "publish" ? "知识文章已发布" : "知识文章已停用");
  } catch (error) {
    ctx.showToast(`审核操作失败：${error.message || error}`, true);
  } finally {
    if (ctx.els.knowledgeList) ctx.els.knowledgeList.setAttribute("aria-busy", "false");
  }
}

/**
 * Bind the knowledge page listeners and island bridges (exactly once, at
 * app.js load). The legacy listeners only reach their DOM in a plain browser
 * tab; the island bridges carry the desktop shell's writes.
 */
export function bindKnowledgeView() {
  if (!ctx?.els) return false;
  const islandMode = () => typeof window !== "undefined" && window.__HELIX_ISLAND_MODE__;
  if (ctx.els.knowledgeSearch) {
    ctx.els.knowledgeSearch.addEventListener("input", () => renderKnowledgeArticles());
  }
  if (ctx.els.knowledgeStatusFilter) {
    ctx.els.knowledgeStatusFilter.addEventListener("change", () => renderKnowledgeArticles());
  }
  if (ctx.els.knowledgeLanguageFilter) {
    ctx.els.knowledgeLanguageFilter.addEventListener("change", () => renderKnowledgeArticles());
  }
  if (ctx.els.newKnowledgeDraft) {
    ctx.els.newKnowledgeDraft.addEventListener("click", () => {
      // The header button is outside the island mount — island mode opens the
      // island's editor instead of the hidden form.
      if (islandMode()) {
        window.dispatchEvent(new CustomEvent(KNOWLEDGE_EVENTS.NEW));
        return;
      }
      resetKnowledgeEditor();
      ctx.els.knowledgeTitle?.focus({ preventScroll: true });
    });
  }
  if (ctx.els.refreshKnowledge) {
    ctx.els.refreshKnowledge.addEventListener("click", () => {
      ctx.state.knowledgeLoadedAt = 0;
      void loadKnowledgeView({ force: true });
    });
  }
  if (ctx.els.cancelKnowledgeEdit) {
    ctx.els.cancelKnowledgeEdit.addEventListener("click", () => resetKnowledgeEditor({ close: true }));
  }
  if (ctx.els.resetKnowledgeForm) {
    ctx.els.resetKnowledgeForm.addEventListener("click", () => resetKnowledgeEditor());
  }
  if (ctx.els.knowledgeForm) {
    ctx.els.knowledgeForm.addEventListener("submit", (event) => void saveKnowledgeArticle(event));
  }
  if (ctx.els.knowledgeList) {
    ctx.els.knowledgeList.addEventListener("click", (event) => {
      const button = event.target.closest(".knowledge-action");
      if (!button) return;
      const articleId = button.dataset.articleId;
      if (button.dataset.action === "edit") editKnowledgeArticle(articleId);
      else void reviewKnowledgeArticle(articleId, button.dataset.action);
    });
  }
  if (typeof window !== "undefined") {
    // Island bridges: the knowledge island owns the surface, the write
    // lifecycle stays here (reviewKnowledgeArticle owns the retire confirm).
    window.addEventListener(KNOWLEDGE_EVENTS.ACTION, (event) => {
      const { action, articleId } = event.detail || {};
      if (!articleId || !["publish", "retire"].includes(action)) return;
      void reviewKnowledgeArticle(articleId, action);
    });
    window.addEventListener(KNOWLEDGE_EVENTS.SAVE, (event) => {
      void saveKnowledgeFromIsland(event.detail || {});
    });
  }
  return true;
}

export default {
  KNOWLEDGE_EVENTS, canWriteKnowledge, syncKnowledgeLanguageSelects, knowledgeFilters,
  renderKnowledgeSummary, knowledgeSourceMarkup, renderKnowledgeArticles,
  updateKnowledgeAccessState, loadKnowledgeView, resetKnowledgeEditor,
  editKnowledgeArticle, saveKnowledgeArticle, saveKnowledgeFromIsland,
  reviewKnowledgeArticle, bindKnowledgeView,
};
