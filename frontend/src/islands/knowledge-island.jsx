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
 * See DESKTOP_TAURI_PLAN.md §3.1 + §D3.
 */

import React, { useReducer, useRef, useCallback, useEffect } from "react";
import { createRoot } from "react-dom/client";
import {
  QueryClient,
  QueryClientProvider,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

/* ── pure domain helpers (verbatim from js/knowledge.js, §43.6) ──────── */

const KNOWLEDGE_STATUSES = ["all", "published", "draft", "pending_review", "retired"];
const KNOWLEDGE_STATUS_LABELS = {
  published: "已发布",
  draft: "草稿",
  pending_review: "待审核",
  retired: "已停用",
};
// Must cover the full app.js LANGUAGE_NAMES set: an article in a language the
// editor select omits would silently lose it on save (review backlog 1).
const LANGUAGE_NAMES = {
  zh: "中文",
  en: "English",
  ja: "日本語",
  ko: "한국어",
  ru: "Русский",
  ar: "العربية",
  hi: "हिन्दी",
  he: "עברית",
  th: "ไทย",
  el: "Ελληνικά",
  es: "Español",
  fr: "Français",
  de: "Deutsch",
  pt: "Português",
};

const LANGUAGE_CODES = Object.keys(LANGUAGE_NAMES).sort((a, b) =>
  LANGUAGE_NAMES[a].localeCompare(LANGUAGE_NAMES[b], "zh"),
);

export const KNOWLEDGE_EVENTS = Object.freeze({
  ACTION: "helix-knowledge-action",
  SAVE: "helix-knowledge-save",
  SAVED: "helix-knowledge-saved",
  NEW: "helix-knowledge-new",
  REFRESH: "helix-knowledge-refresh",
});

/** React-suffixed ids: the yielded legacy editor keeps the originals. */
export const EDITOR_IDS = Object.freeze({
  form: "knowledgeFormReact",
  heading: "knowledgeEditorTitleReact",
  title: "knowledgeTitleReact",
  content: "knowledgeContentReact",
  tags: "knowledgeTagsReact",
  category: "knowledgeCategoryReact",
  language: "knowledgeLanguageReact",
  source: "knowledgeSourceReact",
});

/** Comma/whitespace-separated tags → API list contract (js/knowledge.js). */
function parseKnowledgeTags(value) {
  return [...new Set(String(value || "").split(/[\s,，]+/).map((tag) => tag.trim()).filter(Boolean))];
}

function knowledgeFormPayload(values = {}) {
  return {
    title: String(values.title || "").trim(),
    content: String(values.content || "").trim(),
    tags: parseKnowledgeTags(values.tags),
    category: String(values.category || "general").trim() || "general",
    source_url: String(values.sourceUrl || "").trim(),
    language: String(values.language || "").trim() || null,
  };
}

const EMPTY_DRAFT = Object.freeze({
  title: "",
  content: "",
  tags: "",
  category: "general",
  language: "",
  sourceUrl: "",
});

function draftFromArticle(article) {
  return {
    title: article.title,
    content: article.content,
    tags: article.tags.join(", "),
    category: article.category,
    language: article.language || "",
    sourceUrl: article.source_url,
  };
}

/**
 * Same order and copy as legacy saveKnowledgeArticle: HTML minlength counts
 * raw characters, so a trimmed payload can still fall short of the backend
 * schema (min 2 title / 10 content) — surface it before the 422.
 * @returns {{field: string, message: string}|null}
 */
export function validateKnowledgeDraft(payload) {
  if (!payload.tags.length) return { field: "tags", message: "请至少填写一个知识标签" };
  if (payload.title.length < 2) return { field: "title", message: "标题至少需要 2 个字符" };
  if (payload.content.length < 10) return { field: "content", message: "正文至少需要 10 个字符" };
  return null;
}

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

export function createKnowledgeState() {
  return {
    filters: { status: "all", language: "", query: "" },
    // editor === null keeps the aside hidden, which is what the single-column
    // :has(.knowledge-editor[hidden]) layout rule keys off.
    editor: null,
  };
}

export function reduceKnowledge(state, action) {
  switch (action.type) {
    case "SET_FILTER":
      return { ...state, filters: { ...state.filters, ...action.payload } };
    case "RESET_FILTERS":
      return { ...state, filters: { status: "all", language: "", query: "" } };
    case "OPEN_EDITOR":
      return {
        ...state,
        editor: {
          articleId: action.article ? action.article.id : null,
          values: action.article ? draftFromArticle(action.article) : { ...EMPTY_DRAFT },
          error: null,
          busy: false,
        },
      };
    case "CLOSE_EDITOR":
      return { ...state, editor: null };
    case "SET_FIELD":
      if (!state.editor) return state;
      return {
        ...state,
        editor: {
          ...state.editor,
          values: { ...state.editor.values, [action.name]: action.value },
        },
      };
    // Legacy resetKnowledgeEditor() also drops the editing id, turning the
    // open editor back into a blank new-draft form.
    case "RESET_EDITOR":
      if (!state.editor) return state;
      return { ...state, editor: { articleId: null, values: { ...EMPTY_DRAFT }, error: null, busy: false } };
    case "SET_EDITOR_ERROR":
      if (!state.editor) return state;
      return { ...state, editor: { ...state.editor, error: action.error, busy: false } };
    case "SET_EDITOR_BUSY":
      if (!state.editor) return state;
      return { ...state, editor: { ...state.editor, busy: action.busy } };
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

/**
 * The aside stays in the DOM even when closed: the single-column layout rule
 * is `.knowledge-layout:has(.knowledge-editor[hidden])`, so dropping the
 * element would leave the list squeezed beside a blank 420px column. The form
 * itself only mounts while open, which also gives the title its focus-on-open
 * for free (legacy editKnowledgeArticle/newKnowledgeDraft both focus it).
 */
function KnowledgeEditor({ editor, dispatch, onSubmit }) {
  return (
    <aside className="knowledge-editor" hidden={!editor} aria-label="知识草稿编辑器">
      {editor && <KnowledgeEditorForm editor={editor} dispatch={dispatch} onSubmit={onSubmit} />}
    </aside>
  );
}

function KnowledgeEditorForm({ editor, dispatch, onSubmit }) {
  const titleRef = useRef(null);
  const editing = Boolean(editor.articleId);

  useEffect(() => {
    titleRef.current?.focus({ preventScroll: true });
  }, []);

  const field = (name) => ({
    value: editor.values[name],
    onChange: (e) => dispatch({ type: "SET_FIELD", name, value: e.target.value }),
  });

  // Keep an unmapped language (ru/ar/… beyond the list, or a code the backend
  // added later) selectable so editing never silently drops it.
  const current = editor.values.language;
  const codes = current && !LANGUAGE_CODES.includes(current) ? [...LANGUAGE_CODES, current] : LANGUAGE_CODES;

  return (
    <form id={EDITOR_IDS.form} onSubmit={onSubmit} data-busy={String(editor.busy)} aria-busy={editor.busy}>
      <header className="knowledge-editor-heading">
        <div>
          <span className="section-kicker">EDITOR</span>
          <h3 id={EDITOR_IDS.heading}>{editing ? "编辑知识文章" : "新建知识草稿"}</h3>
        </div>
        <button
          className="icon-button"
          type="button"
          title="关闭编辑器"
          aria-label="关闭编辑器"
          onClick={() => dispatch({ type: "CLOSE_EDITOR" })}
        >
          <svg className="icon" aria-hidden="true"><use href="/static/icons.svg?v=1.4.0#x" /></svg>
        </button>
      </header>
      {editor.error && (
        <p className="knowledge-editor-error" role="alert">{editor.error.message}</p>
      )}
      <label className="knowledge-field">标题
        <input id={EDITOR_IDS.title} ref={titleRef} type="text" minLength={2} maxLength={160} required {...field("title")} />
      </label>
      <label className="knowledge-field">正文
        <textarea id={EDITOR_IDS.content} rows={10} minLength={10} maxLength={12000} required {...field("content")} />
      </label>
      <div className="knowledge-form-grid">
        <label className="knowledge-field">标签
          <input id={EDITOR_IDS.tags} type="text" maxLength={820} placeholder="shipping, refund" required {...field("tags")} />
        </label>
        <label className="knowledge-field">分类
          <input id={EDITOR_IDS.category} type="text" minLength={2} maxLength={60} required {...field("category")} />
        </label>
        <label className="knowledge-field">语言
          <select id={EDITOR_IDS.language} {...field("language")}>
            <option value="">不限语言</option>
            {codes.map((code) => (
              <option value={code} key={code}>{LANGUAGE_NAMES[code] || code}</option>
            ))}
          </select>
        </label>
      </div>
      <label className="knowledge-field">来源
        <input id={EDITOR_IDS.source} type="text" minLength={1} maxLength={500} placeholder="https://… 或 internal:…" required {...field("sourceUrl")} />
      </label>
      <div className="knowledge-editor-actions">
        <button className="button button-secondary" type="button" onClick={() => dispatch({ type: "RESET_EDITOR" })}>
          重置
        </button>
        <button className="button button-primary" type="submit" disabled={editor.busy}>
          <svg className="icon" aria-hidden="true"><use href="/static/icons.svg?v=1.4.0#check" /></svg>
          <span>{editing ? "保存修改" : "保存草稿"}</span>
        </button>
    </div>
    </form>
  );
}

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
