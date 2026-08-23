# API Integration Guide

How to integrate with the Helix Support HTTP API. Companion docs:
[API reference](reference.md), [error contract](../ERRORS.md),
[versioning & deprecation policy](../API_POLICY.md).

## 1. Authentication

Every `/api/*` request carries an API key:

```
X-API-Key: <api-key>
X-Tenant-Id: <tenant-id>      # optional; defaults to the key's tenant
```

The key is bound to a tenant and a role (`admin` / `supervisor` /
`operator` / `channel` / `viewer`). A key cannot act on another tenant; a
mismatched `X-Tenant-Id` returns `403 forbidden`. Invalid keys return
`401 unauthorized`. Each operation's permission requirement is documented
in the [API reference](reference.md) description.

## 2. Idempotency

State-changing writes (`POST /api/conversations/{id}/messages`,
`POST /api/conversations/{id}/turn-jobs`) accept an `Idempotency-Key`
header (8–128 chars of `[A-Za-z0-9._:-]`). Replaying the same key returns
the original result without side effects. Using the same key with a
different payload returns `409 idempotency_conflict`.

Retry pattern: generate a key per logical operation, reuse it across
retries, and discard it once the operation succeeds.

## 3. Pagination

List endpoints (conversations, messages, audit events, quality buckets,
webhook deliveries) use opaque keyset cursors:

```
GET /api/conversations?limit=50
> X-Next-Cursor: eyJ2IjoxLC...
> X-Has-More: true
```

Follow with `GET /api/conversations?limit=50&cursor=<X-Next-Cursor>`.
Cursors are valid only for the same filter set — change filters, drop the
cursor. Do not parse cursor contents; they are opaque. `offset` remains for
compatibility but is not recommended for deep pages.

## 4. Streaming (SSE)

Two SSE endpoints:

- `GET /api/turn-jobs/{job_id}/events` — async turn progress. Events:
  `snapshot` (job state + result when completed), `ping` (keepalive),
  `timeout` (server closed a long-poll; reconnect), `error`.
- `GET /api/events/queue` — queue live updates (`queue` events).

Client pattern: connect, read events, on `timeout`/disconnect reconnect by
fetching the canonical resource first.

## 5. Web Chat client

The customer client is available at `/widget`. A tenant backend signs a
short-lived bootstrap token with `WIDGET_SECRET` and passes it only in the URL
fragment (`/widget?...#token=...`). The browser creates a session with that
token, then replaces it with the response's conversation-bound `widget_token`.
Session message/history/stream calls must use that fresh token in
`X-Widget-Token`; a token for another conversation returns 404.

Messages use a unique `channel_message_id` and `?async_mode=true`. Consume
`GET /api/widget/sessions/{id}/stream` with authenticated streaming `fetch`,
not native `EventSource` (which cannot set the token header). Internal notes
are never returned by the widget history endpoint.

## 6. Webhook signature verification

Outbound webhook deliveries are HMAC-SHA256 signed:

```
X-Helix-Timestamp: <unix seconds>
X-Helix-Signature: <hex hmac>
```

The signed message is `f"{timestamp}." + body` (raw request body bytes).
Verify with the endpoint's secret, and reject timestamps outside a small
window (e.g. ±5 minutes) to prevent replay. Deduplicate on `event_id` in
the JSON body — delivery is at-least-once.

```python
from helix_client import verify_webhook_signature

assert verify_webhook_signature(
    secret=endpoint_secret,
    timestamp=headers["X-Helix-Timestamp"],
    body=request.body,
    signature=headers["X-Helix-Signature"],
)
```

## 7. Formal inbound channel webhook

The provider-neutral reference endpoint is
`POST /api/channels/{account_id}/webhook`. Each `account_id` is configured
server-side with exactly one `tenant_id`, channel name, and secret. The JSON
body cannot select or override a tenant. Prefer `CHANNEL_WEBHOOKS_FILE` (a
gitignored or secret-manager-mounted JSON file) over inline
`CHANNEL_WEBHOOKS_JSON` in production:

```json
{
  "support-main": {
    "tenant_id": "tenant-a",
    "channel": "formal_chat",
    "secret": "at-least-32-random-bytes"
  }
}
```

Send the exact request body with `X-Helix-Timestamp` (Unix seconds) and
`X-Helix-Signature: sha256=<hex>`. The signature is HMAC-SHA256 over
`<timestamp>.<raw-body>`; timestamps outside
`CHANNEL_WEBHOOK_REPLAY_WINDOW_SECONDS` (default 300 seconds) are rejected.
The reference event shape is:

```json
{
  "event": "message.created",
  "message_id": "provider-message-1",
  "thread_id": "provider-thread-1",
  "customer_id": "CUST-1001",
  "customer_name": "Channel Customer",
  "content": "配送一般多久能到"
}
```

`thread_id` is durably mapped to one internal conversation. `message_id` is
claimed account-wide and propagated into the turn job and customer message;
retries return the original `job_id`/turn with `idempotent_replay: true`.
Reusing an id with different content, thread, or customer identity returns a
conflict. The endpoint also applies conversation quota and async queue
backpressure, reopens a resolved mapped conversation, and returns `202` for
accepted work.

## 8. Errors

All errors are [RFC 9457 Problem Details](../ERRORS.md): a JSON body with
`type/title/status/detail/instance` plus `request_id` and `code`. Dispatch
on `code` (stable) or `status`, never on `detail` wording. 429 responses
carry `Retry-After`.

## 9. Example: order lookup flow

1. `POST /api/conversations` with `customer_ref` → conversation id.
2. `POST /api/conversations/{id}/messages` with the question and an
   `Idempotency-Key`.
3. Read `conversation.status`:
   - `open` — automated answer (check `assistant_message.metadata.agent`).
   - `waiting_human` — escalated; `metadata.handoff_reason` explains why.
4. Rate the answer with `POST /api/conversations/{id}/feedback`.

## 10. Python SDK

The maintained SDK (`clients/python/`) wraps all of the above: retries on
transient 5xx, `idempotency_key` support, SSE parsing, typed errors, and
webhook verification. See [clients/python/README.md](../../clients/python/README.md).
