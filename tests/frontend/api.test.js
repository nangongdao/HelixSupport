// Helix Support — api module unit tests (Phase 26.1)
// Run: node --test tests/frontend/api.test.js

import { test } from "node:test";
import assert from "node:assert/strict";

import { request, api, apiWithHeaders, toErrorView, newIdempotencyKey } from "../../app/static/js/api.js";

function okResponse(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async json() {
      return body;
    },
  };
}

test("request returns parsed data on success", async () => {
  const fetchImpl = async () => okResponse({ id: "c1" });
  const { response, data } = await request("/api/conversations", {}, fetchImpl);
  assert.equal(response.ok, true);
  assert.equal(data.id, "c1");
});

test("request sends X-Tenant-Id header", async () => {
  let seen = null;
  const fetchImpl = async (path, options) => {
    seen = options.headers;
    return okResponse([]);
  };
  await request("/api/x", { tenantId: "acme" }, fetchImpl);
  assert.equal(seen["X-Tenant-Id"], "acme");
});

test("request adds Content-Type for bodies", async () => {
  let seen = null;
  const fetchImpl = async (path, options) => {
    seen = options.headers;
    return okResponse({});
  };
  await request("/api/x", { body: { a: 1 } }, fetchImpl);
  assert.equal(seen["Content-Type"], "application/json");
});

test("request serializes object bodies", async () => {
  let body = null;
  const fetchImpl = async (path, options) => {
    body = options.body;
    return okResponse({});
  };
  await request("/api/x", { body: { a: 1 } }, fetchImpl);
  assert.equal(body, JSON.stringify({ a: 1 }));
});

test("request throws with Problem Details detail on error", async () => {
  const fetchImpl = async () =>
    okResponse({ detail: "Conversation not found", code: "not_found", request_id: "req_1" }, 404);
  await assert.rejects(
    () => request("/api/x", {}, fetchImpl),
    (error) => {
      assert.equal(error.message, "Conversation not found");
      assert.equal(error.status, 404);
      assert.equal(error.code, "not_found");
      assert.equal(error.requestId, "req_1");
      return true;
    },
  );
});

test("request throws a generic message when body is not JSON", async () => {
  const fetchImpl = async () => ({ ok: false, status: 500, async json() { throw new Error("bad"); } });
  await assert.rejects(
    () => request("/api/x", {}, fetchImpl),
    /Request failed \(500\)/,
  );
});

test("request throws on abort timeout", async () => {
  const fetchImpl = async (_path, options) => {
    await new Promise((resolve) => setTimeout(resolve, 5));
    if (options.signal?.aborted) {
      const error = new Error("aborted");
      error.name = "AbortError";
      throw error;
    }
    return okResponse({});
  };
  await assert.rejects(
    () => request("/api/x", { timeoutMs: 1 }, fetchImpl),
    /timed out/i,
  );
});

test("api returns only the data", async () => {
  const fetchImpl = async () => okResponse({ n: 5 });
  assert.equal((await api("/api/x", {}, fetchImpl)).n, 5);
});

test("apiWithHeaders returns {response, data}", async () => {
  const fetchImpl = async () => okResponse({ n: 5 });
  const result = await apiWithHeaders("/api/x", {}, fetchImpl);
  assert.equal(result.data.n, 5);
  assert.equal(result.response.ok, true);
});

test("toErrorView normalizes an Error", () => {
  const error = new Error("boom");
  error.status = 429;
  error.code = "rate_limited";
  assert.deepEqual(toErrorView(error), {
    message: "boom",
    status: 429,
    code: "rate_limited",
  });
});

test("toErrorView handles a bare Error", () => {
  assert.deepEqual(toErrorView(new Error("x")), { message: "x", status: null, code: null });
});

test("api module re-exports newIdempotencyKey", () => {
  assert.match(newIdempotencyKey(), /^[0-9a-f]{16}$/);
});
