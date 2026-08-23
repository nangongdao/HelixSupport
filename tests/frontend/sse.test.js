// Helix Support — sse module unit tests (Phase 26.1)
// Run: node --test tests/frontend/sse.test.js

import { test } from "node:test";
import assert from "node:assert/strict";

import { createSseConnection } from "../../app/static/js/sse.js";

/** A fake EventSource that records handlers and exposes close(). */
class FakeSource {
  constructor(url) {
    this.url = url;
    this.closed = false;
    this.onopen = null;
    this.onerror = null;
    this.onmessage = null;
  }
  close() {
    this.closed = true;
  }
  emit(type, data) {
    this.onmessage({ type, data: typeof data === "string" ? data : JSON.stringify(data) });
  }
  fail() {
    this.onerror(new Event("error"));
  }
}

function makeConnection({ handlers = {}, random = () => 0.5 } = {}) {
  const created = [];
  const conn = createSseConnection({
    url: "/api/events/queue",
    createSource: (url) => {
      const source = new FakeSource(url);
      created.push(source);
      return source;
    },
    handlers,
    random,
    baseBackoffMs: 1000,
    maxBackoffMs: 30000,
  });
  return { conn, created };
}

test("connect creates an EventSource and reports connected", () => {
  const { conn, created } = makeConnection();
  conn.connect();
  assert.equal(created.length, 1);
  assert.equal(created[0].url, "/api/events/queue");
  created[0].onopen();
  assert.equal(conn.connected, true);
});

test("onStatus(true) fires when the stream opens", () => {
  let status = null;
  const { conn, created } = makeConnection({ handlers: { onStatus: (s) => (status = s) } });
  conn.connect();
  created[0].onopen();
  assert.equal(status, true);
});

test("parsed JSON events reach onEvent", () => {
  const events = [];
  const { conn, created } = makeConnection({ handlers: { onEvent: (t, d) => events.push([t, d]) } });
  conn.connect();
  created[0].emit("queue", { count: 3 });
  assert.deepEqual(events, [["queue", { count: 3 }]]);
});

test("non-JSON event data is passed through as-is", () => {
  const events = [];
  const { conn, created } = makeConnection({ handlers: { onEvent: (t, d) => events.push([t, d]) } });
  conn.connect();
  created[0].emit("ping", "plain");
  assert.deepEqual(events, [["ping", "plain"]]);
});

test("a failed stream reconnects with backoff", () => {
  const { conn, created } = makeConnection({ random: () => 0.5 });
  conn.connect();
  created[0].fail();
  assert.equal(conn.connected, false);
  assert.equal(created.length, 1); // no immediate second source
});

test("reconnect after backoff creates a second source", async () => {
  const { conn, created } = makeConnection({ random: () => 0.5 });
  conn.connect();
  created[0].fail();
  await new Promise((resolve) => setTimeout(resolve, 800));
  assert.equal(created.length, 2);
});

test("disconnect stops reconnection", async () => {
  const { conn, created } = makeConnection({ random: () => 0.5 });
  conn.connect();
  created[0].fail();
  conn.disconnect();
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(created.length, 1);
});

test("disconnect closes the active source", () => {
  const { conn, created } = makeConnection();
  conn.connect();
  conn.disconnect();
  assert.equal(created[0].closed, true);
  assert.equal(conn.connected, false);
});

test("connect after disconnect is a no-op", () => {
  const { conn, created } = makeConnection();
  conn.connect();
  conn.disconnect();
  conn.connect();
  assert.equal(created.length, 1);
});
