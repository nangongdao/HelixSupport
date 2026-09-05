# Deployment Guide

## Local Runtime

1. Copy `.env.example` to `.env` and keep `APP_ENV=development`, `AUTH_MODE=demo`.
2. Install with `python -m pip install -e .`.
3. Start with `python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`.
4. Check `/health/live`, `/health/ready`, then open `/`.

On Windows, `powershell.exe -NoLogo -NoProfile -NonInteractive -File .\scripts\start_local.ps1` starts a hidden local process, chooses 8000 (or 8001 if occupied), waits for readiness, and prints its URL/PID.

## Desktop App (Tauri, v1.4.0-desktop)

Windows 桌面档位以 Tauri 2.x 原生壳替代浏览器：Python sidecar 随包捆绑并由 Rust supervisor 编排（动态端口、就绪探测、崩溃自愈、单实例锁），数据落在 `%APPDATA%/HelixSupport/data/support.db`。打包、签名、自动更新与冷启动验收见 [`DEPLOYMENT_DESKTOP.md`](DEPLOYMENT_DESKTOP.md)。

## Container Runtime

```powershell
docker compose up --build
```

The bundled SQLite deployment intentionally runs one Uvicorn worker. The named volume stores the database under `/var/lib/helix`. This mode is appropriate for evaluation and controlled single-node pilots.

## Multi-Instance Deployment (PostgreSQL + Redis)

```dotenv
DEPLOYMENT_PROFILE=multi
DATABASE_BACKEND=postgresql
DATABASE_URL=postgresql://helix:...@pg:5432/helix
QUEUE_BACKEND=redis
REDIS_URL=redis://redis:6379/0
QUEUE_FAILURE_MODE=fail_closed
```

`DEPLOYMENT_PROFILE=multi` enforces PostgreSQL + Redis + fail-closed at config
validation and refuses any other combination. With the queue in
`fail_closed` mode the app is **fail-closed**: when Redis is unreachable the
process still boots, `/health/ready` reports `degraded`, every queued intake
answers `503` with `Retry-After: 30`, and the turn worker does not start —
there is **no silent fallback to SQLite** dispatch. Run at least two app
instances (each worker thread derives a unique `worker_id`, so leases never
collide) plus Redis and PostgreSQL for HA; the Redis queue recovers abandoned
claims after their lease expires.

## Session Auth (OIDC)

```dotenv
ENABLE_SESSION_AUTH=true
OIDC_ISSUER_URL=https://.../.well-known/openid-configuration
OIDC_CLIENT_ID=...
OIDC_CLIENT_SECRET=...
BASE_URL=https://support.example.com
```

Enabling session auth activates the Authorization Code + PKCE flow behind a
backend-for-frontend login. Callback state is one-time and bound to
`nonce`/PKCE verifier/`redirect_uri`, RS256-only with kid-rotated JWKS;
identity binds only through verified claims + `tenant_members`. Set `BASE_URL`
to the externally reachable origin — a wrong value breaks the callback `state`.

## DSR 导出（加密）

```dotenv
DSR_EXPORT_SECRET=<长随机值；缺失时 DSR 导出 501 fail-closed>
```

DSR 导出物以 `DSR_EXPORT_SECRET` 派生的 Fernet key 加密存储；下载 token 一次性、
≤15 分钟，导出对象 ≤24 小时。仅为内部 key 的浏览器旅程/自动化可以省略，生产必须设置。

## SQLite Performance Settings

The default single-instance profile uses four pooled connections and short tenant-scoped read caches:

```dotenv
DATABASE_POOL_SIZE=4
DATABASE_BUSY_TIMEOUT_MS=5000
KNOWLEDGE_CACHE_TTL_SECONDS=30
DASHBOARD_CACHE_TTL_SECONDS=5
CACHE_MAX_ENTRIES=512
TURN_WORKER_ENABLED=true
TURN_WORKER_CONCURRENCY=1
TURN_JOB_POLL_INTERVAL_MS=200
TURN_JOB_LEASE_SECONDS=300
TURN_JOB_MAX_ATTEMPTS=3
TURN_JOB_RETRY_BASE_SECONDS=2
TURN_JOB_RETENTION_DAYS=30
TURN_JOB_STREAM_ENABLED=true
TURN_JOB_STREAM_PACING_MS=10
WEBHOOK_DELIVERY_INTERVAL_SECONDS=30
PROMPT_CANARY_RATIO=0.0
CLAIM_TTL_SECONDS=900
OTEL_EXPORTER_OTLP_ENDPOINT=
OTEL_SERVICE_NAME=helix-support
```

Keep `DATABASE_POOL_SIZE` bounded (normally 2-8) and continue to run one Uvicorn worker. Cache entries are invalidated by application writes and expire automatically; direct out-of-process database writers are not supported. Pool and cache statistics are available under `GET /api/system/metrics` for supervisor/admin roles.

## OpenTelemetry Tracing

The app emits an OpenTelemetry-compatible span per HTTP request (method, path, route, status code, duration) and forwards it to an OTLP collector when configured. Install the optional SDK and set the collector endpoint:

```bash
pip install -e '.[otel]'
export OTEL_EXPORTER_OTLP_ENDPOINT=http://collector:4318
export OTEL_SERVICE_NAME=helix-support
```

Spans are exported to `{OTEL_EXPORTER_OTLP_ENDPOINT}/v1/traces`; without the endpoint set (or without the SDK installed) the app silently keeps its zero-dependency lightweight tracing and metric registry, and `GET /api/system/metrics` still surfaces the in-process counters and histograms under `telemetry`.

## Durable Turn Jobs

Long-running customer turns can be submitted without holding an HTTP request open:

```http
POST /api/conversations/{conversation_id}/turn-jobs
Idempotency-Key: a-client-generated-key
```

The API returns `202 Accepted` and a `Location` header. Poll `GET /api/turn-jobs/{job_id}` or subscribe to `GET /api/turn-jobs/{job_id}/events`; the SSE stream emits `token` events (one per assistant output token, with a monotonic `seq`), then a terminal `job` event on completion or failure, plus `ping` and bounded `timeout` events, and closes on completion or failure. The completed response contains the same `TurnResponse` shape as the synchronous message endpoint. Repeating the same tenant, conversation, and idempotency key returns the existing job. A mismatched payload with a reused key is rejected with `409`.

Assistant output is persisted as ordered chunks in the `turn_job_chunks` table (migration 5; a monotonic `seq` column filled by a trigger on SQLite and a sequence on PostgreSQL). The worker writes one chunk per token while pacing at `TURN_JOB_STREAM_PACING_MS` (default 10ms); set `TURN_JOB_STREAM_ENABLED=false` to disable chunk persistence entirely (clients then receive only the terminal `job` event). Idempotent replays never duplicate chunks.

The worker persists state before execution, claims jobs transactionally, retries transient failures with bounded exponential backoff, and recovers abandoned `processing` jobs on startup. `GET /api/system/metrics` exposes tenant queue counts and worker activity. `POST /api/turn-jobs/{job_id}/retry` requeues a terminal failure after an operator or integration has addressed the cause.

Completed and failed job envelopes are retained for `TURN_JOB_RETENTION_DAYS` (30 by default) and cleaned in bounded batches. Conversation messages and audit events have independent retention requirements and are not removed by job cleanup.

The SQLite profile still supports one application process and one database writer topology. Keep `TURN_WORKER_CONCURRENCY` conservative; for multiple processes or replicas, switch to the PostgreSQL backend described below.

## Connectors And Outbound Webhooks

Default Order / Knowledge / CRM connectors are sandbox implementations backed by the application store. Production integrations implement the Protocols in `app/connectors.py` and can start from the HMAC-signed HTTP reference in `app/connectors_http.py`. Connector secrets (base URL, HMAC secret) come from environment or a gitignored secrets file — never from committed config.

The resilient wrappers (`app/connectors_runtime.py`) apply per-tenant circuit breakers and retry only transient errors:

- Knowledge open/empty → built-in FTS fallback; do not invent citations.
- Order / CRM `unavailable` → escalate to a human. Do not report “not found” and do not leak another customer's order status.
- An open breaker returns the sentinel without calling the inner connector again (no duplicate business operation).

Conformance lives in `tests/test_connectors.py` (parameterized mixins for sandbox and HTTP). Fault-injection paths are in `tests/test_connector_degradation.py`.

Outbound webhooks are registered per tenant (`POST /api/webhooks`, `admin:manage`). The worker scans SLA breaches and delivers due payloads every `WEBHOOK_DELIVERY_INTERVAL_SECONDS` (default 30). Each POST is HMAC-SHA256 signed (`X-Helix-Timestamp` + `X-Helix-Signature` over `timestamp.body`). Delivery is at-least-once: `(endpoint_id, event_id)` is unique so re-emits are no-ops, and consumers must treat `event_id` as the deduplication key. Transient HTTP/network failures retry with exponential backoff; exhausted attempts move to `dead`. A delivery stuck in `sending` is reclaimed after the sending lease. Consumer verification example lives in `clients/python/`.

## API Integration

- Static API reference (generated from the OpenAPI contract snapshot): `docs/api/reference.md`.
- Integration guide (auth, idempotency, pagination, SSE streaming, webhook verification): `docs/api/guide.md`.
- Error contract (RFC 9457 Problem Details + error catalog): `docs/ERRORS.md`.
- Versioning & deprecation policy: `docs/API_POLICY.md`.
- Python client SDK (installable package): `clients/python/` — covers conversations, messages, turn jobs + SSE, feedback, knowledge, retries, idempotency keys, and webhook signature verification.
- Contract gate: `api/openapi.json` is the committed OpenAPI snapshot; `scripts/openapi_snapshot.py` fails CI on breaking changes (regenerate with `--dump` only for intentional API changes).

## PostgreSQL Backend

Set two variables to run against a shared PostgreSQL database, which allows several application instances to serve the same tenants:

```bash
DATABASE_BACKEND=postgresql
DATABASE_URL="host=db.internal port=5432 user=helix password=... dbname=helix"
```

`psycopg2` is required (`pip install psycopg2-binary`). The schema, compatibility functions, and triggers are created automatically on first start; `initialize()` is idempotent, so restarts and rolling deploys are safe.

Both backends share one implementation: `PostgresDatabase` subclasses the SQLite `Database` and overrides only connection handling, so every query method — tenant scoping, optimistic concurrency, idempotency guards, queue claiming — is the same code on both. `app/pg_dialect.py` translates the SQLite SQL (placeholders, `INSERT OR IGNORE`, `PRAGMA`, `rowid`) and `app/pg_compat.py` installs server-side equivalents of the SQLite built-ins and triggers. `tests/test_postgres.py` asserts signature parity between the two classes, so a change to one backend cannot silently diverge from the other.

Two behavioural differences are inherent to the engines rather than defects:

- **Full-text search.** FTS5 is SQLite-only. On PostgreSQL, knowledge search uses the same tag/title fallback the SQLite backend uses when FTS is unavailable, which matches on tags and titles rather than body text.
- **Locale collation.** PostgreSQL sorts text by database locale while SQLite sorts by byte order, so text rows tied on a sort key may come out in a different order. Message and audit transcripts do not have this ambiguity: a monotonic `seq` column (SQLite's implicit `rowid`; a sequence-backed column on PostgreSQL) breaks same-instant ties identically on both backends, so ordering and cursor pagination are deterministic.

Verify a deployment against a real instance before cutting over:

```bash
HELIX_PG_INTEGRATION=1 DATABASE_URL="..." python -m unittest tests.test_postgres
```

These integration tests drop and recreate the `public` schema, so point them at a scratch database, never a production one.

### PostgreSQL Tuning (§18.3)

Reference capacity target (see ROADMAP §18.5): one 4C8G instance with PostgreSQL. The settings below are for that shape; the tables named are the ones the §18.2d audit found hot (queue reads, message paging) plus the chunk/journal hot spots.

```sql
-- Heap fillfactor: conversations are UPDATE-heavy (status, priority, labels,
-- claim state) and every update relocates the row; leaving 20-30% page slack
-- avoids heap extension and index page splits under queue churn.
ALTER TABLE conversations SET (fillfactor = 75);
ALTER TABLE messages          SET (fillfactor = 100);  -- append-only rows
ALTER TABLE turn_job_chunks   SET (fillfactor = 100);  -- appended, then only pruned by retention
ALTER TABLE audit_events      SET (fillfactor = 100);  -- append-only

-- Autovacuum: default scale_factor (0.2) fires far too late once a table is
-- at the 100k-row/million-message scale — bloat grows between passes. Lower
-- thresholds so dead rows (updated conversations, pruned chunks) are reclaimed
-- in small, predictable sweeps instead of long blocking ones.
ALTER SYSTEM SET autovacuum_vacuum_scale_factor = 0.05;
ALTER SYSTEM SET autovacuum_vacuum_threshold      = 3000;
ALTER SYSTEM SET autovacuum_analyze_scale_factor  = 0.05;
ALTER SYSTEM SET autovacuum_analyze_threshold     = 3000;
ALTER SYSTEM SET autovacuum_max_workers           = 4;
```

Notes:

- **Messages and audit rows are immutable once written**, so keep `fillfactor =
  100` there; a lower value only wastes pages. Reserve slack for the mutable
  `conversations` row and the quality aggregate buckets.
- **Turn chunks are written one transaction per token** by the streaming
  worker (§18.2c takeover-sink writes the first chunk immediately, then paces).
  On PostgreSQL this is many small commits; keep `synchronous_commit = on`
  (the default) for durability — the latency is amortized by WAL in one commit
  group, unlike the Windows-SQLite fsync hotspot measured in `docs/PERF_NOTES.md`.
- Validate at scale before cutover with the synthetic pagination harness:

```bash
# SQLite smoke (thousands of rows, seconds)
python scripts/pagination_load_test.py --scale smoke
# Full 100k-conversation / 1M-message dataset against PostgreSQL
DATABASE_BACKEND=postgresql DATABASE_URL="..." python scripts/pagination_load_test.py --scale full
```

## High-Volume Queue Reads

`GET /api/conversations` keeps the original `offset` contract and adds an opaque `cursor` continuation. A page that has more rows exposes `X-Next-Cursor`; do not send `cursor` and a non-zero `offset` together. Cursors are tied to the current sort position, not a snapshot, so restart from the first page after changing any filter. Supported indexed filters include status, priority, channel, operator assignment, active claim, unassigned/unclaimed state, SLA breach, label, and FTS search.

Message search uses the synchronized `message_fts` table when FTS5 is available. Queue rows read `preview`, `message_count`, and `last_message_at` from incrementally maintained conversation projections, so large queues no longer run a preview/count subquery per row.

Long threads can use `GET /api/conversations/{conversation_id}/messages` with opaque `cursor`, `before`, and `X-Prev-Cursor` / `X-Next-Cursor` headers instead of loading the full history.

## Conversation Archiving

Resolved conversations are a cold, append-only liability: once a case is closed it never changes again, yet it keeps occupying queue index pages and `message_fts` rows. ROADMAP 18.3 moves them to a read-only archive tier so the hot tables track only live work.

```dotenv
CONVERSATION_ARCHIVE_ENABLED=true
CONVERSATION_ARCHIVE_AFTER_DAYS=180
CONVERSATION_ARCHIVE_BATCH=100
CONVERSATION_ARCHIVE_CADENCE_HOURS=6
```

The turn worker runs an archive pass on the fixed cadence and moves up to `CONVERSATION_ARCHIVE_BATCH` conversations per pass: resolved conversations whose `resolved_at` is older than `CONVERSATION_ARCHIVE_AFTER_DAYS` (and with no `queued`/`processing` turn jobs) are copied transactionally — conversation row, messages, labels, and feedback — into the `*_archive` tables and then physically deleted from the hot tables. The message deletes cascade into `message_fts` via the existing triggers, which is how the tier bounds index and FTS volume. The move is all-or-nothing per batch and idempotent across replays; `GET /api/system/metrics` reports `archived_total`.

The read path stays transparent (`GET /api/conversations/{id}`, `GET .../messages`, and `GET /api/conversations?archived=true` for retrospection) serve archived conversations; `archived=true` searches fall back to LIKE because archived messages were evicted from the FTS mirror by design. The write path is hot-only: inserting a message into, claiming, or resolving an archived conversation fails with 404. Set `CONVERSATION_ARCHIVE_ENABLED=false` to disable the tier entirely.

## Claims And Canned Responses

`POST /api/conversations/{id}/claim` reserves an unresolved conversation for `CLAIM_TTL_SECONDS`; repeating by the same operator renews the claim. Another operator receives `409`, while supervisors/admins can override. `POST /api/conversations/{id}/release` returns it to the queue. Accepting or resolving clears the claim atomically.

Tenant canned responses are listed at `GET /api/canned-responses`. Supervisors/admins manage them with `POST` and `PATCH`; operators record an insertion with `POST /api/canned-responses/{id}/use`. Shortcut uniqueness is enforced per tenant for active entries.

Supervisor/admin audit export is available from `GET /api/audit-events` with conversation, event type, time, limit, and offset filters. Results remain tenant-scoped and audit payloads retain secret redaction.

Retention enforcement for `audit_events` is archive-first: expired rows are written
in bounded transactions to `audit_archives` with the original `seq`, hash-chain
links, canonical manifest JSON and SHA-256 digest; only after the manifest is read
back successfully are the hot rows deleted. `GET /api/audit-archives` lists the
tenant-scoped manifests and `GET /api/audit-archives/{archive_id}` returns one
verifiable export. `scripts/verify_audit_chain.py` merges cold and hot rows by
sequence before checking the chain and rejects tampered archive content,
manifest boundaries, or duplicate ids/sequences. New audit writes continue from
the newest cold/hot chain boundary even when the hot table was emptied. Data
subject deletion removes operational conversation data but does not rewrite
independently retained audit evidence; audit rows expire only through the
tenant's `audit_events` retention policy.

## Labels And Bulk Actions

Operators can replace labels with `PUT /api/conversations/{conversation_id}/labels`, inspect counts with `GET /api/conversation-labels`, filter with `label=`, and apply up to 100 IDs through `POST /api/conversations/bulk-actions`. Bulk actions are `set_priority`, `add_labels`, and `remove_labels`; cross-tenant or unknown IDs are counted as unmatched and never disclosed individually. Every actual change produces an audit event.

## Production Configuration

Set `APP_ENV=production`, `AUTH_MODE=api_key`, and inject the API-key configuration from a secret manager. Two equivalent sources are accepted; `API_KEYS_FILE` takes precedence:

- `API_KEYS_JSON` — the JSON document inline, e.g. from a secret-manager-injected environment variable.
- `API_KEYS_FILE` — a path to a gitignored JSON file holding the same document. Prefer this when the deployment tool can mount a secrets file but not put secrets in the process environment or shell history; a rotated file is picked up at restart. The file is read and validated at startup (`Settings.validate()` fails fast on an unreadable or malformed file).

Each credential maps to exactly one tenant, actor, and role:

```json
{
  "a-long-random-secret-value": {
    "tenant_id": "tenant-a",
    "actor_id": "ops.admin",
    "role": "admin"
  }
}
```

Never place production credentials in the static operator UI. Deploy the UI behind an OIDC-aware backend-for-frontend or identity proxy that supplies the authenticated API context. Terminate TLS at a trusted reverse proxy and preserve `X-Request-Id`.

For the customer Web Chat client, set a long random `WIDGET_SECRET` and an
exact `WIDGET_FRAME_ANCESTORS` allowlist such as
`'self',https://help.example.com`. The latter applies only to `/widget`;
production startup refuses wildcard ancestors. The embedding backend must
issue short-lived bootstrap tokens and put them in the iframe URL fragment,
never query parameters or server-rendered logs. See
[`docs/OPERATIONS.md`](docs/OPERATIONS.md#web-chat-widget) for the integration
flow.

### Control-Plane And Widget Secret Requirements (2.3.0 tightening)

Production startup fails fast on two secret rules (enforced by
`Settings.validate()`):

1. `WIDGET_SECRET` must be replaced with a long random value. The built-in
   development default (`helix-widget-dev-secret`) lets anyone forge widget
   tokens and is rejected in production.
2. `CONTROL_PLANE_SECRET` must be set explicitly. Without it the control
   plane would fall back to the development widget secret; production
   refuses that fallback. Generate at least 32 random bytes — a shorter
   value disables the tenant control plane entirely (signed policy
   snapshots and restricted-field envelope encryption depend on it), and
   the process logs `control_plane.disabled` while request-path model
   governance keeps its fail-open pre-43.5 behavior.


For a formal inbound messaging channel, configure the provider account map
with `CHANNEL_WEBHOOKS_FILE` (preferred) or `CHANNEL_WEBHOOKS_JSON`. Each map
entry binds one account id to a tenant, channel, and at least 32 random bytes;
the request body cannot choose a tenant:

```json
{
  "support-main": {
    "tenant_id": "tenant-a",
    "channel": "formal_chat",
    "secret": "replace-with-at-least-32-random-bytes"
  }
}
```

The provider posts `message.created` events to
`/api/channels/support-main/webhook` with
`X-Helix-Timestamp` and `X-Helix-Signature: sha256=<hex>`. The exact raw body
is signed as `<timestamp>.<body>` using HMAC-SHA256. Keep the default
five-minute replay window unless the provider's delivery SLA requires a
different value. The service durably maps `thread_id`, claims `message_id`
account-wide, and carries the channel id through the turn worker; repeated
delivery is acknowledged without a second conversation message. Mount the
JSON file read-only and rotate it through the deployment secret manager.
See [docs/api/guide.md](docs/api/guide.md#7-formal-inbound-channel-webhook)
for the event schema and conflict semantics.

## Scale-Out Gate

Do not add Uvicorn workers or replicas while using SQLite. Switch `DATABASE_BACKEND` to `postgresql` (see above), use Redis or another durable queue for slow Agent work, and move rate limits/metrics out of process. Run connector contract tests and idempotency race tests before enabling concurrency.

## Backup And Restore

- Stop writes or use SQLite's online backup API before copying the database.
- Retain the database, WAL, and SHM consistently if copying at filesystem level.
- Test restoration into an isolated path and call `/health/ready` before switching traffic.
- For production PostgreSQL, use encrypted automated backups, point-in-time recovery, and quarterly restore drills.

## Release Gate

- Unit/API tests, browser smoke, compile, lint, type check, dependency audit
- Production configuration startup test
- Golden support-question regression set and unsafe-answer threshold
- Backup restore and rollback rehearsal
- Load, accessibility, abuse, and prompt-injection tests
- On-call owner, SLA policy, retention policy, and incident runbook approval
