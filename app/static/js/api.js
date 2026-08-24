/**
 * Helix Support — api module (Phase 26.1)
 *
 * Request layer with timeout, error normalization (RFC 9457 Problem
 * Details), and an injectable fetch for tests. The legacy app.js keeps its
 * own request()/api() copies; this module is the single source for tests
 * and the migration target.
 */

import { newIdempotencyKey } from "./format.js?v=1.3.9";

/**
 * Perform an API request with timeout and Problem Details error parsing.
 * @param {string} path
 * @param {object} options fetch options + { tenantId, timeoutMs }
 * @param {function} [fetchImpl] injectable fetch for tests
 * @returns {Promise<{response: Response, data: any}>}
 */
export async function request(path, options = {}, fetchImpl = fetch) {
  const {
    tenantId = "demo",
    timeoutMs = 15000,
    headers: extraHeaders,
    body,
    ...rest
  } = options;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const requestHeaders = {
    Accept: "application/json",
    "X-Tenant-Id": tenantId,
    ...(body ? { "Content-Type": "application/json" } : {}),
    ...(extraHeaders || {}),
  };
  try {
    const response = await fetchImpl(path, {
      ...rest,
      headers: requestHeaders,
      signal: controller.signal,
      ...(body !== undefined ? { body: typeof body === "string" ? body : JSON.stringify(body) } : {}),
    });
    if (!response.ok) {
      let payload = {};
      try {
        payload = await response.json();
      } catch {
        payload = {};
      }
      const detail = payload.detail || payload.title || `Request failed (${response.status})`;
      const error = new Error(detail);
      error.status = response.status;
      error.code = payload.code || "http_error";
      error.requestId = payload.request_id || null;
      throw error;
    }
    const data = response.status === 204 ? null : await response.json();
    return { response, data };
  } catch (error) {
    if (error.name === "AbortError") throw new Error("Request timed out");
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

/** Convenience: return only the parsed data. */
export async function api(path, options = {}, fetchImpl = fetch) {
  const result = await request(path, options, fetchImpl);
  return result.data;
}

/** Convenience: return {response, data} (for headers/cursor reads). */
export async function apiWithHeaders(path, options = {}, fetchImpl = fetch) {
  return request(path, options, fetchImpl);
}

/**
 * Normalize an API error into a stable object for UI toasts.
 * @param {Error} error
 * @returns {{message: string, status: number|null, code: string|null}}
 */
export function toErrorView(error) {
  return {
    message: error.message || "Unknown error",
    status: error.status ?? null,
    code: error.code ?? null,
  };
}

export { newIdempotencyKey };

export default { request, api, apiWithHeaders, toErrorView, newIdempotencyKey };
