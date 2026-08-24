# Helix Client (Python SDK)

Thin, typed client for the Helix Support customer-service API. Covers
conversations, messages, async turn jobs with SSE streaming, feedback,
knowledge drafts, and webhook signature verification. Depends only on
`httpx`.

## Install

```bash
pip install -e clients/python
```

## Quick start

```python
from helix_client import HelixClient

with HelixClient(base_url="http://localhost:8000", api_key="sk-...", tenant_id="demo") as client:
    conv = client.create_conversation(customer_name="Ada", customer_ref="CUST-1")
    turn = client.send_message(conv["id"], "ORD-10482 到哪了？", idempotency_key="idem-1")
    print(turn["assistant_message"]["content"])
    print(turn["conversation"]["status"])
```

## Async turn jobs + SSE streaming

```python
job = client.create_turn_job(conv["id"], "退货政策是什么？", idempotency_key="idem-2")
for event in client.stream_turn_job(job["id"]):
    if event["event"] == "snapshot" and event["data"].get("status") == "completed":
        print(event["data"]["result"]["assistant_message"]["content"])
        break
```

## API v2 (cursor envelopes)

The v1 API stays fully supported; the v2 methods below hit `/api/v2/*`,
which returns cursor-envelope responses (`{"data": [...], "next_cursor":
...}`) and discloses its version via the `X-API-Version` header.

```python
# One page, with envelope + server version exposed.
page = client.list_conversations_v2(limit=50, sort="updated", status="open")
print(page.data, page.next_cursor, page.api_version)  # api_version == "2.0"

# Or walk every conversation without manual cursor bookkeeping.
for conv in client.iter_conversations_v2(limit=200):
    print(conv["id"], conv["status"])

page = client.get_conversation_v2(conv["id"])  # single resource
page = client.list_messages_v2(conv["id"], limit=100)  # keyset pagination
```

v2 creation accepts an `Idempotency-Key`. Replays return the original
resource, and the client flags them:

```python
conv = client.create_conversation_v2("Ada", idempotency_key="order-42")
print(conv["_idempotent_replay"])  # False on first call, True on replay
```

Core fields of a v2 conversation are byte-identical to v1 (shadow-read
contract), so switching between versions is safe per read.

## Errors

Every HTTP error raises a `HelixError` subclass carrying the RFC 9457
Problem Details fields (`status`, `code`, `request_id`, `instance`). 429
raises `HelixRateLimitError` with `retry_after`.

```python
from helix_client import HelixNotFoundError

try:
    client.get_conversation("conv-nope")
except HelixNotFoundError as exc:
    print(exc.status, exc.code, exc.request_id)
```

## Webhook signature verification

```python
from helix_client import verify_webhook_signature

ok = verify_webhook_signature(
    secret="endpoint-secret",
    timestamp=request.headers["X-Helix-Timestamp"],
    body=request.body,
    signature=request.headers["X-Helix-Signature"],
)
```

Also reject stale timestamps (replay window) in your consumer.

## Retries

Transient 5xx responses are retried up to `max_retries` times (default 3)
with no backoff; 429 errors are surfaced immediately with `Retry-After`.
Pass `idempotency_key` on message/turn-job writes so retried calls stay
side-effect free.
