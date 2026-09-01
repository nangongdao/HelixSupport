/**
 * Helix Support — knowledge island pure domain helpers.
 *
 * Verbatim from js/knowledge.js (§43.6 framework-agnostic): status/language
 * catalogues, tag parsing, the API payload shape, article normalization,
 * filtering, the summary counters and the per-status review actions. Split
 * out of knowledge-island.jsx when it crossed the 400-line module limit.
 */

export const KNOWLEDGE_STATUSES = ["all", "published", "draft", "pending_review", "retired"];

export const KNOWLEDGE_STATUS_LABELS = {
  published: "已发布",
  draft: "草稿",
  pending_review: "待审核",
  retired: "已停用",
};

// Must cover the full app.js LANGUAGE_NAMES set: an article in a language the
// editor select omits would silently lose it on save (review backlog 1).
export const LANGUAGE_NAMES = {
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

export const LANGUAGE_CODES = Object.keys(LANGUAGE_NAMES).sort((a, b) =>
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
export function parseKnowledgeTags(value) {
  return [...new Set(String(value || "").split(/[\s,，]+/).map((tag) => tag.trim()).filter(Boolean))];
}

export function knowledgeFormPayload(values = {}) {
  return {
    title: String(values.title || "").trim(),
    content: String(values.content || "").trim(),
    tags: parseKnowledgeTags(values.tags),
    category: String(values.category || "general").trim() || "general",
    source_url: String(values.sourceUrl || "").trim(),
    language: String(values.language || "").trim() || null,
  };
}

export const EMPTY_DRAFT = Object.freeze({
  title: "",
  content: "",
  tags: "",
  category: "general",
  language: "",
  sourceUrl: "",
});

export function draftFromArticle(article) {
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

export function normalizeKnowledgeArticle(article = {}) {
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

export function matchesKnowledgeArticle(article, filters = {}) {
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

export function filterKnowledgeArticles(articles, filters = {}) {
  return (Array.isArray(articles) ? articles : []).map(normalizeKnowledgeArticle).filter((a) => matchesKnowledgeArticle(a, filters));
}

export function summarizeKnowledgeArticles(articles) {
  const summary = { total: 0, published: 0, draft: 0, pending_review: 0, retired: 0 };
  for (const article of Array.isArray(articles) ? articles : []) {
    const status = normalizeKnowledgeArticle(article).status;
    summary.total += 1;
    if (Object.hasOwn(summary, status)) summary[status] += 1;
  }
  return summary;
}

export function reviewActionsFor(article) {
  const status = normalizeKnowledgeArticle(article).status;
  if (status === "draft" || status === "pending_review") return ["publish", "retire"];
  if (status === "published") return ["retire"];
  return [];
}
