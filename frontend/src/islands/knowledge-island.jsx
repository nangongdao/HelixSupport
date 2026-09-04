/**
 * Helix Support — knowledge domain React island (D3)
 *
 * Migrates the knowledge operations view to React, using useReducer with
 * the existing pure functions from js/knowledge.js (normalize/filter/
 * summarize/reviewActions — §43.6 framework-agnostic reducer triplets).
 *
 * Mounts into #knowledgeReactIsland and owns the whole knowledge surface:
 * summary + filters + article list + the draft editor (the final D3 slice).
 * Editor writes go through legacy via helix-knowledge-save/-review so the
 * api()/showToast()/reload lifecycle stays in one place; the island keeps
 * the legacy DOM id + label contract (knowledgeEditor/knowledgeTitle/…) so
 * the ui_knowledge and axe keyboard-path locators keep resolving.
 *
 * This file is the composition root: the pure domain helpers, the reducer
 * and the presentational components live under ./knowledge/ (the domain
 * outgrew the project's 400-line module limit). Every previously exported
 * name is re-exported here so importers are unchanged.
 *
 * See DESKTOP_TAURI_PLAN.md §3.1 + §D3.
 */

import React, { useReducer, useCallback, useEffect } from "react";
import { createRoot } from "react-dom/client";
import {
  QueryClient,
  QueryClientProvider,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import {
  EDITOR_IDS,
  KNOWLEDGE_EVENTS,
  filterKnowledgeArticles,
  knowledgeFormPayload,
  normalizeKnowledgeArticle,
  summarizeKnowledgeArticles,
  validateKnowledgeDraft,
} from "./knowledge/domain.js";
import { createKnowledgeState, reduceKnowledge } from "./knowledge/reducer.js";
import {
  KnowledgeArticle,
  KnowledgeEditor,
  KnowledgeSummary,
} from "./knowledge/components.jsx";

export { EDITOR_IDS, KNOWLEDGE_EVENTS, validateKnowledgeDraft } from "./knowledge/domain.js";
export { createKnowledgeState, reduceKnowledge } from "./knowledge/reducer.js";

export function KnowledgeIsland() {
  const [state, dispatch] = useReducer(reduceKnowledge, undefined, createKnowledgeState);
  // knowledge:write maps to admin/platform (matches app.js canWriteKnowledge +
  // backend ROLE_PERMISSIONS); the desktop shell exposes the role globally.
  const role = (typeof window !== "undefined" && window.__HELIX_ROLE__) || "guest";
  const canWrite = ["admin", "platform"].includes(role);

  const queryClient = useQueryClient();
  const queryKey = ["knowledge-articles", canWrite];
  const { data, isLoading, error, refetch } = useQuery({
    queryKey,
    queryFn: async () => {
      // Writers need drafts/retired too, or the summary counters and the
      // editor would only ever see published articles (legacy parity).
      const path = canWrite ? "/api/knowledge?include_inactive=true" : "/api/knowledge";
      const res = await fetch(path, {
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

  const handleAction = useCallback(
    (action, articleId) => {
      if (action === "edit") {
        const raw = articles.find((item) => item.id === articleId);
        if (raw) dispatch({ type: "OPEN_EDITOR", article: normalizeKnowledgeArticle(raw) });
        return;
      }
      // publish/retire keep their legacy home: reviewKnowledgeArticle owns the
      // retire confirm() prompt and the api()/toast/reload lifecycle.
      window.dispatchEvent(
        new CustomEvent(KNOWLEDGE_EVENTS.ACTION, { detail: { action, articleId } }),
      );
    },
    [articles],
  );

  const handleSubmit = useCallback(
    (event) => {
      event.preventDefault();
      const editor = state.editor;
      if (!editor || editor.busy) return;
      const payload = knowledgeFormPayload(editor.values);
      const invalid = validateKnowledgeDraft(payload);
      if (invalid) {
        dispatch({ type: "SET_EDITOR_ERROR", error: invalid });
        document.getElementById(EDITOR_IDS[invalid.field])?.focus();
        return;
      }
      dispatch({ type: "SET_EDITOR_ERROR", error: null });
      dispatch({ type: "SET_EDITOR_BUSY", busy: true });
      window.dispatchEvent(
        new CustomEvent(KNOWLEDGE_EVENTS.SAVE, {
          detail: { payload, editingId: editor.articleId },
        }),
      );
    },
    [state.editor],
  );

  // Legacy still owns the view header buttons (新建草稿 / 刷新) and the write
  // lifecycle, so it drives the island through these three events.
  useEffect(() => {
    const onNew = () => dispatch({ type: "OPEN_EDITOR", article: null });
    // A plain view re-open is not a reason to hit the network: legacy renders
    // from its 15s cache there, and staleTime is the island's equivalent. The
    // `stale: true` filter asks react-query at event time rather than trusting
    // a render-time snapshot, so only an explicit refresh (or a write) always
    // forces the round-trip.
    const onRefresh = (event) => {
      if (event.detail?.force) {
        void refetch();
        return;
      }
      void queryClient.refetchQueries({ queryKey, stale: true });
    };
    const onSaved = (event) => {
      const { ok } = event.detail || {};
      if (!ok) {
        dispatch({ type: "SET_EDITOR_BUSY", busy: false });
        return;
      }
      dispatch({ type: "CLOSE_EDITOR" });
      void refetch();
    };
    window.addEventListener(KNOWLEDGE_EVENTS.NEW, onNew);
    window.addEventListener(KNOWLEDGE_EVENTS.REFRESH, onRefresh);
    window.addEventListener(KNOWLEDGE_EVENTS.SAVED, onSaved);
    return () => {
      window.removeEventListener(KNOWLEDGE_EVENTS.NEW, onNew);
      window.removeEventListener(KNOWLEDGE_EVENTS.REFRESH, onRefresh);
      window.removeEventListener(KNOWLEDGE_EVENTS.SAVED, onSaved);
    };
  }, [refetch]);

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
      {!canWrite && (
        <p className="knowledge-read-only">
          当前角色可检索已发布文章；草稿和审核操作仅对知识管理员开放。
        </p>
      )}
      <div className="knowledge-layout">
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
        {/* Always rendered, hidden when closed — see KnowledgeEditor. Readers
            never open it (no edit button), which matches the legacy aside. */}
        <KnowledgeEditor editor={state.editor} dispatch={dispatch} onSubmit={handleSubmit} />
      </div>
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
