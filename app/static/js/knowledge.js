/**
 * Knowledge operations view helpers (ROADMAP section 17).
 *
 * Pure normalization, filtering, counters, and form projection keep the
 * lifecycle rules independently testable from the legacy DOM controller.
 */

export const KNOWLEDGE_STATUSES = ["all", "published", "draft", "pending_review", "retired"];

export const KNOWLEDGE_STATUS_LABELS = {
  published: "已发布",
  draft: "草稿",
  pending_review: "待审核",
  retired: "已停用",
};

/** Normalize one API article without mutating the response object. */
export function normalizeKnowledgeArticle(article = {}) {
  const status = article.status || (article.active === false ? "retired" : "published");
  const tags = Array.isArray(article.tags)
    ? article.tags
    : String(article.tags || "")
        .split(/[\s,，]+/)
        .filter(Boolean);
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

/** Parse comma/whitespace-separated tags into the API list contract. */
export function parseKnowledgeTags(value) {
  return [...new Set(String(value || "").split(/[\s,，]+/).map((tag) => tag.trim()).filter(Boolean))];
}

/** Project form values into a trimmed API payload. */
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

/** Return whether an article is visible under the current page filters. */
export function matchesKnowledgeArticle(article, filters = {}) {
  const normalized = normalizeKnowledgeArticle(article);
  const status = KNOWLEDGE_STATUSES.includes(filters.status) ? filters.status : "all";
  const language = String(filters.language || "").trim();
  if (status !== "all" && normalized.status !== status) return false;
  // A language-agnostic article (language === null) matches any requested
  // language, mirroring retrieval semantics (app/db/knowledge.py: null gets
  // the best rank under any language) — see review backlog 3.
  if (language && normalized.language && normalized.language !== language) return false;

  const query = String(filters.query || "").trim().toLocaleLowerCase();
  if (!query) return true;
  const haystack = [
    normalized.title,
    normalized.content,
    normalized.category,
    normalized.source_url,
    normalized.language || "",
    ...normalized.tags,
  ]
    .join(" ")
    .toLocaleLowerCase();
  return haystack.includes(query);
}

/** Normalize and filter articles while preserving the API's newest-first order. */
export function filterKnowledgeArticles(articles, filters = {}) {
  return (Array.isArray(articles) ? articles : [])
    .map(normalizeKnowledgeArticle)
    .filter((article) => matchesKnowledgeArticle(article, filters));
}

/** Lifecycle counters for the page summary strip. */
export function summarizeKnowledgeArticles(articles) {
  const summary = { total: 0, published: 0, draft: 0, pending_review: 0, retired: 0 };
  for (const article of Array.isArray(articles) ? articles : []) {
    const status = normalizeKnowledgeArticle(article).status;
    summary.total += 1;
    if (Object.hasOwn(summary, status)) summary[status] += 1;
  }
  return summary;
}

/** Review actions accepted by the current backend state machine. */
export function reviewActionsFor(article) {
  const status = normalizeKnowledgeArticle(article).status;
  if (status === "draft" || status === "pending_review") return ["publish", "retire"];
  if (status === "published") return ["retire"];
  return [];
}

export default {
  KNOWLEDGE_STATUSES,
  KNOWLEDGE_STATUS_LABELS,
  normalizeKnowledgeArticle,
  parseKnowledgeTags,
  knowledgeFormPayload,
  matchesKnowledgeArticle,
  filterKnowledgeArticles,
  summarizeKnowledgeArticles,
  reviewActionsFor,
};
