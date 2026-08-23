import assert from "node:assert/strict";
import test from "node:test";

import {
  copy,
  makeChannelMessageId,
  mergeMessages,
  normalizeAccent,
  normalizeLocale,
  parseSseFrames,
  readWidgetConfig,
  restoreSession,
  restoreSessionForLaunch,
  saveSession,
  tokenPayload,
} from "../../app/static/js/widget-core.js";

test("widget config reads token from fragment and clamps unsafe accent", () => {
  const config = readWidgetConfig("https://help.example/widget?brand=Acme&accent=not-real&locale=en#greeting=nope&token=abc");
  assert.equal(config.brand, "Acme");
  assert.equal(config.accent, "teal");
  assert.equal(config.locale, "en");
  assert.equal(config.token, "abc");
});

test("locale and accent normalization stay inside supported choices", () => {
  assert.equal(normalizeLocale("en-US"), "en");
  assert.equal(normalizeLocale("ja"), "zh");
  assert.equal(normalizeAccent("BLUE"), "blue");
  assert.equal(normalizeAccent("#fff"), "teal");
});

test("localized structural titles are complete", () => {
  assert.equal(copy("en", "chatTitle"), "Support conversation");
  assert.equal(copy("en", "fatalTitle"), "Chat link unavailable");
  assert.equal(copy("zh", "loading"), "正在恢复对话…");
});

test("SSE parser handles split frames, comments and multiline data", () => {
  const first = parseSseFrames(": ping\n\nevent: token\ndata: {\"content\":\"你", false);
  assert.deepEqual(first.events, []);
  const second = parseSseFrames(`${first.remainder}好\"}\n\n`, false);
  assert.deepEqual(second.events, [{ event: "token", data: "{\"content\":\"你好\"}" }]);
});

test("SSE parser flushes a terminal frame without a trailing blank line", () => {
  assert.deepEqual(parseSseFrames("event: job\ndata: {\"status\":\"completed\"}", true).events, [
    { event: "job", data: '{"status":"completed"}' },
  ]);
});

test("message merge is idempotent and hides internal notes", () => {
  const merged = mergeMessages(
    [{ id: "a", role: "assistant", content: "old", created_at: "2026-01-01T00:00:01Z" }],
    [
      { id: "a", role: "assistant", content: "new", created_at: "2026-01-01T00:00:01Z" },
      { id: "secret", role: "internal_note", content: "do not show" },
      { id: "b", role: "customer", content: "hello", created_at: "2026-01-01T00:00:02Z" },
    ],
  );
  assert.deepEqual(merged.map((message) => message.id), ["a", "b"]);
  assert.equal(merged[0].content, "new");
});

test("session storage restores only a valid conversation-bound token", () => {
  const encoded = btoa(JSON.stringify({ tenant_id: "demo", conversation_id: "conv-1", exp: Math.floor(Date.now() / 1000) + 60 }));
  const token = `${encoded}.signature`;
  const values = new Map();
  const storage = {
    getItem: (key) => values.get(key) || null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  };
  saveSession(storage, { conversationId: "conv-1", token });
  assert.equal(restoreSession(storage).conversationId, "conv-1");
  assert.deepEqual(tokenPayload(token).conversation_id, "conv-1");
});

test("an explicit bootstrap token starts a new session instead of restoring stale state", () => {
  const encoded = btoa(JSON.stringify({ tenant_id: "demo", conversation_id: "conv-old", exp: Math.floor(Date.now() / 1000) + 60 }));
  const values = new Map();
  const storage = {
    getItem: (key) => values.get(key) || null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  };
  saveSession(storage, { conversationId: "conv-old", token: `${encoded}.signature` });

  assert.equal(restoreSessionForLaunch(storage, "new-bootstrap-token"), null);
  assert.equal(storage.getItem("helix-widget-session"), null);
});

test("channel ids are stable-shaped and unique", () => {
  const fakeCrypto = { randomUUID: () => "uuid-1" };
  assert.equal(makeChannelMessageId(fakeCrypto), "web-uuid-1");
  assert.match(makeChannelMessageId({}), /^web-/);
});
