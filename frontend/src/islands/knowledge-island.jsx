/**
 * Helix Support — knowledge domain React island (D3)
 *
 * Migrates the knowledge operations view to React, using useReducer with
 * the existing pure functions from js/knowledge.js (normalize/filter/
 * summarize/reviewActions — §43.6 framework-agnostic reducer triplets).
 *
 * Mounts into #knowledgeReactIsland. During the dual-track period the
 * legacy app.js still owns the editor/form; this island owns the article
 * list + summary + filters. Once D3 is complete the legacy rendering
 * functions are deleted and app.js keeps only glue.
 *
 * See DESKTOP_TAURI_PLAN.md §3.1 + §D3.
 */

import React, { useReducer, useState, useCallback, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";

/* ── pure domain helpers (verbatim from js/knowledge.js, §43.6) ──────── */

const KNOWLEDGE_STATUSES = ["all", "published", "draft", "pending_review", "retired"];
const KNOWLEDGE_STATUS_LABELS = {
  published: "已发布",
  draft: "草稿",
  pending_review: "待审核",
  retired: "已停用",
};
const LANGUAGE_NAMES = { zh: "中文", en: "English", ja: "日本語", ko: "한국어", es: "Español", fr: "Français", de: "Deutsch", pt: "Português" };

function normalizeKnowledgeArticle(article = {}) {
  const status = article.status || (article.active === false ? "retired" : "published");
  const tags = Array.isArray(article.tags)
    ? article.tags
    : String(article.tags || "").split(/[\s,，]+/).filter(Boolean);
  return {
    ...article,
    title: String(article.title || ""),
    content: String(article.content || ""),
    category: String(article.category || "general"),
    source_url: String(article.source_url || ""),
    language: article.language || null,
    status,
    tags: [...new Set(tags.map((tag) => String(tag).trim()).filter(Boolean))],
  };
}

function matchesKnowledgeArticle(article, filters = {}) {
  const normalized = normalizeKnowledgeArticle(article);
  const status = KNOWLEDGE_STATUSES.includes(filters.status) ? filters.status : "all";
  const language = String(filters.language || "").trim();
  if (status !== "all" && normalized.status !== status) return false;
  if (language && normalized.language && normalized.language !== language) return false;
  const query = String(filters.query || "").trim().toLocaleLowerCase();
  if (!query) return true;
  const haystack = [normalized.title, normalized.content, normalized.category, normalized.source_url, normalized.language || "", ...normalized.tags].join(" ").toLocaleLowerCase();
  return haystack.includes(query);
}

function filterKnowledgeArticles(articles, filters = {}) {
  return (Array.isArray(articles) ? articles : []).map(normalizeKnowledgeArticle).filter((a) => matchesKnowledgeArticle(a, filters));
}

function summarizeKnowledgeArticles(articles) {
  const summary = { total: 0, published: 0, draft: 0, pending_review: 0, retired: 0 };
  for (const article of Array.isArray(articles) ? articles : []) {
    const status = normalizeKnowledgeArticle(article).status;
    summary.total += 1;
    if (Object.hasOwn(summary, status)) summary[status] += 1;
  }
  return summary;
}

function reviewActionsFor(article) {
  const status = normalizeKnowledgeArticle(article).status;
  if (status === "draft" || status === "pending_review") return ["publish", "retire"];
  if (status === "published") return ["retire"];
  return [];
}

/* ── reducer (§43.6: createState() + reduce(state, action)) ──────────── */

function createKnowledgeState() {
  return {
    filters: { status: "all", language: "", query: "" },
    editingId: null,
  };
}

function reduceKnowledge(state, action) {
  switch (action.type) {
    case "SET_FILTER":
      return { ...state, filters: { ...state.filters, ...action.payload } };
    case "RESET_FILTERS":
      return { ...state, filters: { status: "all", language: "", query: "" } };
    default:
      return state;
  }
}

/* ── React components ─────────────────────────────────────────────────── */

function KnowledgeSummary({ summary, canWrite }) {
  const rows = [
    ["全部", summary.total],
    ["已发布", summary.published],
    ["草稿", canWrite ? summary.draft : "—"],
    ["待审核", canWrite ? summary.pending_review : "—"],
    ["已停用", canWrite ? summary.retired : "—"],
  ];
  return (
    <div className="knowledge-summary" aria-live="polite">
      {rows.map(([label, count]) => (
        <div className="knowledge-summary-item" key={label}>
          <span>{label}</span>
          <strong>{String(count)}</strong>
        </div>
      ))}
    </div>
  );
}

function KnowledgeArticle({ article, canWrite, onAction }) {
  const status = Object.hasOwn(KNOWLEDGE_STATUS_LABELS, article.status) ? article.status : "retired";
  const statusLabel = KNOWLEDGE_STATUS_LABELS[status] || status;
  const language = article.language ? LANGUAGE_NAMES[article.language] || article.language : "通用";
  const tags = article.tags.length
    ? article.tags
    : ["未分类"];
  const reviewActions = canWrite ? reviewActionsFor(article) : [];
  const isUrl = /^https?:\/\//i.test(article.source_url);

  return (
    <article className="knowledge-article" role="listitem" data-article-id={article.id}>
      <div className="knowledge-article-head">
        <h3>{article.title}</h3>
        <span className={`knowledge-status ${status}`}>{statusLabel}</span>
      </div>
      <div className="knowledge-article-meta">
        <span>{article.category}</span><span>·</span>
        <span>{language}</span><span>·</span>
        <span>v{article.version || 1}</span><span>·</span>
        <time>{article.updated_at || ""}</time>
      </div>
      <div className="knowledge-tag-list">
        {tags.map((tag) => (
          <span className="knowledge-tag" key={tag}>{tag}</span>
        ))}
      </div>
      <details>
        <summary>查看正文</summary>
        <p className="knowledge-article-content">{article.content}</p>
      </details>
      {isUrl ? (
        <a className="knowledge-source" href={article.source_url} target="_blank" rel="noopener noreferrer" title={article.source_url}>
          {article.source_url}
        </a>
      ) : (
        <span className="knowledge-source" title={article.source_url}>
          {article.source_url || "未记录来源"}
        </span>
      )}
      {canWrite && (
        <div className="knowledge-article-actions">
          <button type="button" className="knowledge-action" data-action="edit" data-article-id={article.id} onClick={() => onAction("edit", article.id)}>
            编辑
          </button>
          {reviewActions.map((action) => (
            <button key={action} type="button" className="knowledge-action" data-action={action} data-article-id={article.id} onClick={() => onAction(action, article.id)}>
              {action === "publish" ? "发布" : "停用"}
            </button>
          ))}
        </div>
      )}
    </article>
  );
}

function KnowledgeIsland() {
  const [state, dispatch] = useReducer(reduceKnowledge, undefined, createKnowledgeState);
  const [canWrite] = useState(true); // TODO: wire to /api/me role check

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["knowledge-articles"],
    queryFn: async () => {
      const res = await fetch("/api/knowledge", {
        headers: { "X-Tenant-Id": "demo" },
      });
      if (!res.ok) throw new Error(`knowledge API ${res.status}`);
      return res.json();
    },
    staleTime: 30_000,
  });

  const articles = Array.isArray(data) ? data : data?.items || [];
  const filtered = filterKnowledgeArticles(articles, state.filters);
  const summary = summarizeKnowledgeArticles(articles);

  const handleAction = useCallback((action, articleId) => {
    // Delegate to the legacy app.js knowledge action handler via a custom
    // event so the editor/form lifecycle stays in the legacy controller
    // during the dual-track period.
    window.dispatchEvent(
      new CustomEvent("helix-knowledge-action", { detail: { action, articleId } }),
    );
  }, []);

  if (isLoading) {
    return (
      <div className="qc-skeleton" role="status" aria-label="知识库加载中">
        <div className="qc-skeleton-bar" />
        <div className="qc-skeleton-bar" />
      </div>
    );
  }
  if (error) {
    return (
      <div className="qc-error" role="alert">
        知识库加载失败：{String(error.message || error)}
      </div>
    );
  }

  return (
    <div className="knowledge-island">
      <KnowledgeSummary summary={summary} canWrite={canWrite} />
      <div className="knowledge-toolbar" aria-label="知识文章筛选">
        <label className="knowledge-search">
          <span className="sr-only">搜索知识文章</span>
          <input
            type="search"
            maxLength={160}
            autoComplete="off"
            placeholder="搜索标题、正文、标签或来源"
            value={state.filters.query}
            onChange={(e) => dispatch({ type: "SET_FILTER", payload: { query: e.target.value } })}
          />
        </label>
        <label className="knowledge-filter">
          <span>状态</span>
          <select
            value={state.filters.status}
            onChange={(e) => dispatch({ type: "SET_FILTER", payload: { status: e.target.value } })}
          >
            <option value="all">全部状态</option>
            <option value="published">已发布</option>
            <option value="draft">草稿</option>
            <option value="pending_review">待审核</option>
            <option value="retired">已停用</option>
          </select>
        </label>
        <label className="knowledge-filter">
          <span>语言</span>
          <select
            value={state.filters.language}
            onChange={(e) => dispatch({ type: "SET_FILTER", payload: { language: e.target.value } })}
          >
            <option value="">全部语言</option>
            <option value="zh">中文</option>
            <option value="en">English</option>
            <option value="ja">日本語</option>
            <option value="ko">한국어</option>
            <option value="es">Español</option>
            <option value="fr">Français</option>
            <option value="de">Deutsch</option>
            <option value="pt">Português</option>
          </select>
        </label>
        <span className="knowledge-result-count">
          {filtered.length} / {articles.length} 篇
        </span>
      </div>
      <section className="knowledge-list-panel" aria-label="知识文章">
        <div className="knowledge-list-status" role="status">
          {!filtered.length
            ? articles.length
              ? "没有符合当前筛选条件的文章。"
              : "当前租户还没有知识文章。"
            : ""}
        </div>
        <div className="knowledge-list" role="list">
          {filtered.map((raw) => {
            const article = normalizeKnowledgeArticle(raw);
            return <KnowledgeArticle key={article.id} article={article} canWrite={canWrite} onAction={handleAction} />;
          })}
        </div>
      </section>
    </div>
  );
}

/**
 * Mount the knowledge island into a host <div>. Called by the island loader.
 * @param {HTMLElement} element - mount point
 */
export function mount(element) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { refetchOnWindowFocus: false } },
  });
  const root = createRoot(element);
  root.render(
    <QueryClientProvider client={queryClient}>
      <KnowledgeIsland />
    </QueryClientProvider>,
  );
}

export default { mount, KnowledgeIsland };
