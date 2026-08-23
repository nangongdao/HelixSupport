// Knowledge operations pure-module tests (ROADMAP section 17).

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  filterKnowledgeArticles,
  knowledgeFormPayload,
  matchesKnowledgeArticle,
  normalizeKnowledgeArticle,
  parseKnowledgeTags,
  reviewActionsFor,
  summarizeKnowledgeArticles,
} from "../../app/static/js/knowledge.js";

const ARTICLES = [
  {
    id: "published",
    title: "配送时效",
    content: "普通配送通常需要三到五天。",
    tags: ["shipping", "配送"],
    category: "logistics",
    source_url: "https://example.com/shipping",
    language: "zh",
    active: true,
    status: "published",
  },
  {
    id: "draft",
    title: "Refund window",
    content: "Draft refund policy awaiting approval.",
    tags: "refund policy",
    category: "returns",
    source_url: "internal:refund",
    language: "en",
    active: false,
    status: "draft",
  },
  {
    id: "retired",
    title: "旧版发票说明",
    content: "该流程已经停用。",
    tags: ["invoice"],
    category: "billing",
    source_url: "internal:legacy",
    language: null,
    active: false,
    status: "retired",
  },
];

test("normalizeKnowledgeArticle fills lifecycle defaults without mutating input", () => {
  const source = { title: "A", tags: "one two", active: true };
  const normalized = normalizeKnowledgeArticle(source);
  assert.equal(normalized.status, "published");
  assert.deepEqual(normalized.tags, ["one", "two"]);
  assert.equal(source.status, undefined);
  assert.equal(source.tags, "one two");
});

test("parseKnowledgeTags trims, deduplicates, and accepts Chinese commas", () => {
  assert.deepEqual(parseKnowledgeTags(" refund, 配送，refund  urgent "), [
    "refund",
    "配送",
    "urgent",
  ]);
  assert.deepEqual(parseKnowledgeTags(""), []);
});

test("knowledgeFormPayload maps DOM-shaped values to the API contract", () => {
  assert.deepEqual(
    knowledgeFormPayload({
      title: "  Delivery  ",
      content: "  Long enough content  ",
      tags: "shipping, delivery",
      category: "  logistics ",
      sourceUrl: " https://example.com ",
      language: "zh",
    }),
    {
      title: "Delivery",
      content: "Long enough content",
      tags: ["shipping", "delivery"],
      category: "logistics",
      source_url: "https://example.com",
      language: "zh",
    },
  );
});

test("matchesKnowledgeArticle searches title, content, tags, category, and source", () => {
  assert.equal(matchesKnowledgeArticle(ARTICLES[0], { query: "配送" }), true);
  assert.equal(matchesKnowledgeArticle(ARTICLES[0], { query: "logistics" }), true);
  assert.equal(matchesKnowledgeArticle(ARTICLES[0], { query: "example.com" }), true);
  assert.equal(matchesKnowledgeArticle(ARTICLES[0], { query: "refund" }), false);
});

test("filterKnowledgeArticles combines status and language filters", () => {
  assert.deepEqual(
    filterKnowledgeArticles(ARTICLES, { status: "draft", language: "en" }).map(
      (article) => article.id,
    ),
    ["draft"],
  );
  assert.deepEqual(
    filterKnowledgeArticles(ARTICLES, { query: "invoice", status: "retired" }).map(
      (article) => article.id,
    ),
    ["retired"],
  );
});

test("language-agnostic articles stay visible under any language filter", () => {
  // A null-language article matches any requested language (retrieval
  // semantics), while a specific other language still excludes it.
  const zhIds = filterKnowledgeArticles(ARTICLES, { language: "zh" }).map((article) => article.id);
  assert.ok(zhIds.includes("published"), "zh article matches");
  assert.ok(zhIds.includes("retired"), "null-language article stays visible");
  assert.ok(!zhIds.includes("draft"), "en article is excluded");
});

test("summarizeKnowledgeArticles reports all lifecycle counters", () => {
  assert.deepEqual(summarizeKnowledgeArticles(ARTICLES), {
    total: 3,
    published: 1,
    draft: 1,
    pending_review: 0,
    retired: 1,
  });
});

test("reviewActionsFor mirrors the backend lifecycle transitions", () => {
  assert.deepEqual(reviewActionsFor({ status: "draft" }), ["publish", "retire"]);
  assert.deepEqual(reviewActionsFor({ status: "pending_review" }), ["publish", "retire"]);
  assert.deepEqual(reviewActionsFor({ status: "published" }), ["retire"]);
  assert.deepEqual(reviewActionsFor({ status: "retired" }), []);
});
