/**
 * Helix Support — request transport (app.js <500 campaign).
 *
 * The legacy request()/api()/apiWithHeaders() triple, extracted verbatim from
 * app.js so the console keeps its exact request semantics: multipart FormData
 * pass-through, the shared X-Tenant-Id/Accept headers, Chinese error messages.
 * The base headers arrive through configure. js/api.js is the separate
 * test-oriented client from Phase 26.1; this module is the one the running
 * legacy console (and every configure-injected module) actually uses.
 */

let baseHeaders = {};
let timeoutMs = 15000;

/** Inject the shared tenant/Accept headers and request timeout (boot, once). */
export function configure(deps) {
  baseHeaders = deps.baseHeaders || {};
  timeoutMs = deps.timeoutMs ?? 15000;
}

async function request(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  // FormData(multipart 上传)必须由浏览器自动生成 Content-Type 边界;任何
  // JSON 之外的 body(FormData/blob)都不该被覆写为 application/json。
  const isFormData = typeof FormData !== "undefined" && options.body instanceof FormData;
  const requestHeaders = {
    ...baseHeaders,
    ...(!isFormData && options.body ? { "Content-Type": "application/json" } : {}),
    ...(options.headers || {}),
  };
  try {
    const response = await fetch(path, {
      ...options,
      headers: requestHeaders,
      signal: controller.signal,
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.detail || `请求失败（${response.status}）`);
    }
    const data = response.status === 204 ? null : await response.json();
    return { response, data };
  } catch (error) {
    if (error.name === "AbortError") throw new Error("请求超时，请稍后重试");
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

async function api(path, options = {}) {
  const result = await request(path, options);
  return result.data;
}

async function apiWithHeaders(path, options = {}) {
  return request(path, options);
}

export { request, api, apiWithHeaders };
export default { request, api, apiWithHeaders };