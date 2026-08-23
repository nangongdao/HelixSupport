/**
 * Helix Support — sse module (Phase 26.1)
 *
 * SSE connection lifecycle with exponential-backoff reconnect. Pure logic:
 * the transport is injected so tests can simulate event streams without a
 * real EventSource. The legacy app.js keeps its own connectQueueEvents();
 * this module is the single source for tests and the migration target.
 */

import { backoffDelay } from "./format.js?v=1.3.7";

/**
 * Create an SSE connection manager.
 * @param {object} opts
 * @param {string} opts.url
 * @param {function} [opts.createSource] returns an EventSource-like object
 *   (onopen/onmessage/onerror/close). Defaults to the global EventSource.
 * @param {object} [opts.handlers] { onEvent(type, data), onStatus(connected) }
 * @param {function} [opts.random] injectable RNG for backoff jitter
 * @param {number} [opts.baseBackoffMs]
 * @param {number} [opts.maxBackoffMs]
 * @returns {{connect: function, disconnect: function, readonly connected: boolean}}
 */
export function createSseConnection({
  url,
  createSource,
  handlers = {},
  random = Math.random,
  baseBackoffMs = 1000,
  maxBackoffMs = 30000,
}) {
  let source = null;
  let attempt = 0;
  let reconnectTimer = null;
  let stopped = false;
  let isConnected = false;

  function onOpen() {
    isConnected = true;
    attempt = 0;
    if (handlers.onStatus) handlers.onStatus(true);
  }

  function onError() {
    isConnected = false;
    if (handlers.onStatus) handlers.onStatus(false);
    if (source) {
      try {
        source.close();
      } catch {
        /* already closed */
      }
      source = null;
    }
    if (stopped) return;
    const delay = backoffDelay(attempt, baseBackoffMs, maxBackoffMs, random);
    attempt += 1;
    reconnectTimer = setTimeout(() => {
      if (!stopped) connect();
    }, delay);
  }

  function onMessage(event) {
    if (!handlers.onEvent) return;
    let data = null;
    try {
      data = event.data ? JSON.parse(event.data) : null;
    } catch {
      data = event.data;
    }
    handlers.onEvent(event.type || "message", data);
  }

  function connect() {
    if (stopped) return;
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    const factory = createSource || ((u) => new EventSource(u));
    source = factory(url);
    source.onopen = onOpen;
    source.onerror = onError;
    source.onmessage = onMessage;
  }

  function disconnect() {
    stopped = true;
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (source) {
      try {
        source.close();
      } catch {
        /* already closed */
      }
      source = null;
    }
    isConnected = false;
  }

  return {
    connect,
    disconnect,
    get connected() {
      return isConnected;
    },
  };
}

export default { createSseConnection };
