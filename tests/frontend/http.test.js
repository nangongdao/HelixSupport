// Helix Support — request transport unit tests (app.js <500 slice 15)
// Run: node --test tests/frontend/http.test.js

import { afterEach, test } from "node:test";
import assert from "node:assert/strict";

import { api, apiWithHeaders, configure, request } from "../../app/static/js/http.js";

const BASE_HEADERS = { Accept: "application/json", "X-Tenant-Id": "demo" };

function configureClient() {
  configure({ baseHeaders: BASE_HEADERS });
}

/** Minimal Response stub with the fields request() reads. */
function responseStub({ status = 200, body = {} } = {}) {
  return {
    ok: status < 400,
    status,
    headers: { get: (name) => (name === "X-Prev-Cursor" ? "next-cursor" : null) },
    json: async () => body,
  };
}

const originalFetch = globalThis.fetch;
function stubFetch(impl) {
  globalThis.fetch = impl;
}
afterEach(() => {
  globalThis.fetch = originalFetch;
});

test("request merges base headers, sets JSON Content-Type for a string body", async () => {
  configureClient();
  const calls = [];
  stubFetch(async (path, init) => {
    calls.push({ path, init });
    return responseStub({ body: { id: "c1" } });
  });
  const { response, data } = await request("/api/conversations", {
    method: "POST",
    body: JSON.stringify({ name: "x" }),
  });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].init.headers["X-Tenant-Id"], "demo");
  assert.equal(calls[0].init.headers.Accept, "application/json");
  assert.equal(calls[0].init.headers["Content-Type"], "application/json");
  assert.equal(calls[0].init.signal instanceof AbortSignal, true);
  assert.deepEqual({ response: response.ok, data }, { response: true, data: { id: "c1" } });
});

test("request skips Content-Type for multipart FormData bodies", async () => {
  configureClient();
  const calls = [];
  stubFetch(async (path, init) => {
    calls.push({ init });
    return responseStub({ status: 201, body: { id: "a1" } });
  });
  const form = new FormData();
  await request("/api/attachments", { method: "POST", body: form });
  assert.equal(calls.length, 1);
  assert.equal("Content-Type" in calls[0].init.headers, false, "browser owns the multipart boundary");
});

test("api returns the parsed data while apiWithHeaders returns the envelope", async () => {
  configureClient();
  stubFetch(async () => responseStub({ body: { ok: true } }));
  assert.deepEqual(await api("/api/me"), { ok: true });
  const withHeaders = await apiWithHeaders("/api/conversations?limit=1");
  assert.equal(withHeaders.response.headers.get("X-Prev-Cursor"), "next-cursor");
  assert.deepEqual(withHeaders.data, { ok: true });
});

test("a 204 response yields null data", async () => {
  configureClient();
  stubFetch(async () => responseStub({ status: 204, body: null }));
  assert.equal(await api("/api/attachments/1", { method: "DELETE" }), null);
});

test("a non-ok response surfaces its Problem Details detail in Chinese context", async () => {
  configureClient();
  stubFetch(async () => responseStub({ status: 422, body: { detail: "配额已满" } }));
  await assert.rejects(
    () => api("/api/admin/tenants/demo/quota", { method: "PUT" }),
    (error) => error.message === "配额已满",
  );
});

test("a non-ok response without detail falls back to the status text", async () => {
  configureClient();
  stubFetch(async () => responseStub({ status: 500, body: {} }));
  await assert.rejects(
    () => api("/api/conversations"),
    (error) => error.message === "请求失败（500）",
  );
});

test("an abort is rethrown as the operator-facing timeout message", async () => {
  configureClient();
  stubFetch(async () => {
    throw Object.assign(new Error("aborted"), { name: "AbortError" });
  });
  await assert.rejects(
    () => api("/api/conversations"),
    (error) => error.message === "请求超时，请稍后重试",
  );
});