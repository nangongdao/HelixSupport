# Operations Runbook

## Health And Signals

- `GET /health/live`: process is serving requests.
- `GET /health/ready`: database is reachable.
- `GET /api/system/metrics`: request latency, database pool/cache/search, and tenant turn-job/worker statistics, supervisor/admin only.
- `GET /api/dashboard`: queue, SLA, confidence, grounding, and feedback indicators.
- `GET /api/supervisor/quality`: daily quality buckets (turn count, escalation rate, negative-feedback rate, avg first response, avg latency, estimated tokens) with keyset pagination (`X-Next-Cursor`/`X-Has-More`), `metrics:read` RBAC. Use `intent`/`prompt_version`/`since`/`until` filters to drill into a quality regression.
- `GET /api/supervisor/knowledge-gaps`: conversations whose negative feedback had no knowledge citation — the starting point for a knowledge-base reflow. Each row carries the rated message id so a draft article can be created from it.
- `GET /api/admin/usage`: raw daily usage rows (conversation/turn/message counts) for billing reconciliation, `tenant:manage` RBAC.
- Every response includes `X-Request-Id`; audit events persist the same identifier.
- `PROCESS_ROLE` (Phase 42.1) splits the runtime role of a process: `all` (default, single-process dev/legacy) serves the API and runs the turn worker + housekeeping; `web` is a stateless API tier that never starts the worker (scale the web fleet without adding workers); `worker` runs the worker and is expected not to expose public business endpoints at the deploy-time network layer. `/health/ready` reports `process_role` and `runs_turn_worker`; `/api/admin/diagnostics` reports `process_role` in `config`. The role never changes the endpoint surface inside the app — restricting worker ingress is a network/ingress concern.

## Tenant Operations

### Provisioning A Tenant

`POST /api/admin/tenants` (`tenant:manage`) idempotently creates a tenant,
seeds baseline knowledge articles so it is immediately serviceable, and sets
quota/policy columns. Re-posting the same `tenant_id` is a no-op for the row
and re-applies quota fields. Verify with `GET /api/admin/tenants/{id}/quota`
and a knowledge search (`/api/knowledge?q=配送` under that tenant).

### Member Lifecycle

Members live in `tenant_members` (status: `invited`/`active`/`deactivated`).
Inviting an existing member resets them to `invited`; a role change PATCH
moves them to `active`; deactivation retains conversation history and audit
events (`member.*`). Deactivated members no longer satisfy the membership
check at authentication, so they lose access immediately while their data
stays for compliance.

### Quota And Metering

- Conversation quota: `POST /api/conversations` counts active
  (`status != 'resolved'`) conversations and returns `429 rate_limited` with
  `Retry-After` when the tenant's `conversation_quota` is exhausted;
  resolving a conversation frees a slot.
- Usage rows accumulate atomically per (tenant, day) on conversation
  creation and every message write; `GET /api/admin/usage` exports them for
  billing. Reconcile against `audit_events` (`conversation.created`,
  `turn.processed`) — the counts are independent writes, so a mismatch
  points at a caller retrying without an idempotency key.
- Raise limits with `PUT /api/admin/tenants/{id}/quota`; there is no
  auto-extension, so monitor `tenant_usage_daily` growth alongside the
  quota to avoid surprise 429s.

## Web Chat Widget

The customer client is served at `GET /widget`; its endpoints
(`/api/widget/sessions*`) authenticate via signed customer tokens instead of
an API key. A short-lived bootstrap token carries the tenant id and optional
customer reference. `POST /api/widget/sessions` exchanges it for a fresh token
bound to that exact conversation; only the fresh token can send, stream, or
read the session. Both tokens expire after one hour with a clock-skew guard.

- **Issuing tokens**: the embedding page must obtain a token from a trusted
  issuer (the tenant's backend) signed with `WIDGET_SECRET`; never ship the
  secret in the browser. A leaked secret lets anyone open sessions for that
  tenant — rotate `WIDGET_SECRET` and re-issue tokens on exposure.
- **Embedding**: set `WIDGET_FRAME_ANCESTORS` to a comma-separated list of
  exact parent origins (`'self',https://help.example.com`). Only `/widget`
  receives that CSP `frame-ancestors` policy; the operator workspace remains
  `X-Frame-Options: DENY` / `frame-ancestors 'none'`. Production rejects `*`.
  Pass the bootstrap token in the URL fragment so it never enters HTTP logs or
  referrers. Supported theme inputs are `brand`, `greeting`, `locale=zh|en`,
  and `accent=teal|blue|amber|violet|coral`:

  ```html
  <iframe
    title="客户服务"
    src="https://support.example.com/widget?brand=Northstar%20Care&accent=teal&locale=zh#token=SIGNED_BOOTSTRAP_TOKEN"
    width="380"
    height="700"
    referrerpolicy="no-referrer"
  ></iframe>
  ```

  Generate the iframe URL per authenticated customer request; do not place a
  real token in static HTML, analytics, or client logs.
- **Channel idempotency**: the widget sends `channel_message_id` with each
  message. Replaying the same id returns the original turn
  (`idempotent_replay: true`) and never creates a second turn; the unique
  index `idx_messages_channel_dedup` enforces this at the database level.
- **Streaming**: `GET /api/widget/sessions/{id}/stream` serves SSE
  token/job events for the conversation's latest turn job; the client uses
  `?async_mode=true` on message send, then parses the stream through `fetch()`
  because native `EventSource` cannot attach `X-Widget-Token`.
- **Privacy**: the session token is kept in `sessionStorage` for reload
  recovery, never `localStorage`; message history filters internal notes at
  the server boundary and again in the client renderer.
- **Human takeover**: when a turn escalates, `conversation.status` becomes
  `waiting_human` and further widget messages are stored but not answered
  by automation — the operator flow is unchanged.

## Formal Inbound Channel Webhook

The provider-neutral endpoint is `POST /api/channels/{account_id}/webhook`.
Configure each account in a secret-manager-mounted `CHANNEL_WEBHOOKS_FILE`;
the server-side record, never the request body, selects the tenant and channel.
Keep `data/secrets/` gitignored and restrict the mounted file to the service
identity.

- **Authentication**: sign the exact raw body as HMAC-SHA256 over
  `<unix_timestamp>.<body>`, then send `X-Helix-Timestamp` and
  `X-Helix-Signature: sha256=<hex>`. Do not parse and reserialize the JSON
  between signing and delivery. The default replay window is 300 seconds.
- **Retry contract**: retry the identical body with the same `message_id`.
  A successful replay returns the original `job_id` with
  `idempotent_replay: true`; changing the thread, customer, or body for that
  id returns 409. Respect 429 `Retry-After` without generating a new id.
- **Diagnosis**: repeated 401 responses indicate unknown account, stale clock,
  or bad signature by design; the response does not distinguish them. Check
  the account mapping, sender/service NTP, and raw-body bytes without logging
  the secret or customer content. Use request ids and `conversation.created`,
  `turn_job.queued`, and lifecycle audit events for reconciliation.
- **Rotation**: replace the account secret in the mounted file, restart every
  instance, then send a newly timestamped signed probe. The reference config
  supports one active secret per account, so coordinate the sender cutover
  with the restart and retain receipt/job records for retry reconciliation.

## Incident Actions

### Model Provider Failure

Semantic triage catches provider transport/response errors and uses deterministic routing. Set `ENABLE_LLM=false` and restart if provider failures create latency. Knowledge answers and order tools do not require the provider.

### Escalation Spike

Check `policy.assessed`, `agent.routed`, and `quality.reviewed` events. Separate policy-risk, missing-knowledge, identity-binding, and confidence-gate causes before changing thresholds. Do not lower the quality threshold without a golden-set regression — run `python scripts/evaluate.py` (requires no live model; deterministic routing) and the `tests/test_golden_set.py` gate before any prompt or routing change.

### Quality Regression Investigation

When `/api/supervisor/quality` shows a rising escalation or negative-feedback rate for a specific `intent`/`prompt_version`, compare the same intent across prompt versions with the `prompt_version` filter — a canary whose rate diverges from the active version is a signal to roll back the canary (`POST /api/prompts/{id}/action` with `rollback`). A tenant whose `allowed_models` excludes the prompt's `model_ref` degrades to deterministic routing and records a `turn.model_denied` audit event; check that event when a tenant's quality profile changes without a prompt change. Knowledge gaps list negative-feedback turns without citations; publish a draft (created from the gap) only after review — drafts are invisible to retrieval until approved.

### Webhook Delivery Backlog

`webhook_deliveries` rows in `pending`/`sending` are delivered by the worker housekeeping scan. A row stuck in `sending` past the lease (`SENDING_LEASE_SECONDS`, default 60s) is reclaimed by any worker. Permanent consumer errors (4xx outside the retryable set) dead-letter immediately; transient 5xx retry with jittered exponential backoff up to `max_attempts` then dead-letter. Deleting an endpoint dead-letters its pending deliveries. SLA-breach events carry the breach deadline in `event_id`, so a conversation that breaches twice (resolved, reopened, breached again) emits two distinct events.

### Database Busy Or Unready

Stop extra application workers; SQLite mode supports one application process. Check `database.pool` for saturation, keep the configured pool bounded, and confirm disk space and volume permissions. Restore from a verified backup if integrity checks fail. Migrate to PostgreSQL before sustained concurrent writes or multiple instances.

### Cache Hit Rate Collapse

Inspect `database.cache` in system metrics. Repeated misses immediately after writes are expected because mutations invalidate tenant entries. Sustained misses without writes usually indicate TTL values that are too short or a workload with too many tenants for `CACHE_MAX_ENTRIES`; adjust conservatively and monitor memory before increasing limits.

### Turn Queue Backlog

Inspect `turn_jobs.queued`, `turn_jobs.processing`, `turn_jobs.failed`, and `oldest_queued_age_seconds` in system metrics. A growing queue usually means the model provider or an external connector is slow. Check `turn_worker.active_workers`, `retried_total`, and audit events `turn_job.retry_scheduled` / `turn_job.failed` before increasing concurrency. Keep the lease at least as long as the idempotency processing timeout; do not run a second SQLite application process. Terminal jobs can be retried through `POST /api/turn-jobs/{job_id}/retry` after the underlying issue is fixed.

`TURN_JOB_RETENTION_DAYS` controls terminal job-envelope cleanup. Worker metrics expose `pruned_total`; cleanup does not delete conversation messages or audit events.

### Queue Backend Unreachable (Multi-Instance)

In multi-instance mode (`DEPLOYMENT_PROFILE=multi`) the queue backend is Redis
and `QUEUE_FAILURE_MODE=fail_closed` is enforced at startup. When Redis becomes
unreachable the app **stays up and degrades deliberately**: `/health/ready`
returns 503 with `degraded_reason` and `last_success_epoch`, every queued turn
intake answers `503` + `Retry-After: 30` with code `queue_unavailable`, and the
turn worker does not start. There is no silent SQLite fallback — do not look
for one; any "working" dispatch path outside the Redis backend is a defect.
Restore Redis and confirm `/health/ready` goes green before resuming traffic;
the same fail-closed app recovers in place, and abandoned claims are reclaimed
after their lease expires. Run the failure drill with
`python scripts/redis_failure_drill.py` (needs a local `redis-server` on an
ephemeral scratch port; exits 0 when all four classes pass).

### Knowledge Retrieval

SQLite builds with FTS5 use the synchronized `knowledge_fts` index and BM25 ranking. `database.knowledge_search.fts5_enabled`, `fts_queries`, and `fallback_queries` show the active path. If FTS5 is unavailable, the deterministic cached scorer remains safe but should be treated as a scale limitation. Disable or update an article through the API so the index triggers and tenant cache invalidation run together.

### Queue Search And Pagination

Inspect `database.message_search.fts5_enabled`, `fts_queries`, and `fallback_queries` when message search latency changes. The operator console uses `X-Next-Cursor`; a client that changes filters must discard its old cursor. Offset remains available for compatibility but should not be used for deep pages. If summary counts or previews look stale after an import, run the application migration once and verify the `messages_summary_insert` trigger before accepting writes.

### Bulk Classification

Bulk priority and label mutations require `operator:act`, are limited to 100 IDs per request, and report `requested`, `matched`, `updated`, and `unchanged`. Review `conversation.priority_changed` and `conversation.labels_changed` audit events when reconciling operator actions. A low `matched` count is expected when an integration submits IDs from another tenant; do not turn that count into an existence oracle.

### Claims And Assignment

`conversation.claimed` and `conversation.claim_released` events record operator reservations. Claims expire after `CLAIM_TTL_SECONDS`, are ignored after expiration, and are cleared when a conversation is accepted or resolved. Use `claimed_by`, `unclaimed`, `mine`, and `unassigned` queue filters to reconcile ownership. A `409` during claim/accept normally means another operator owns the active claim.

### Job Event Streams

`GET /api/turn-jobs/{job_id}/events` emits SSE `job` snapshots, keepalive `ping` events, and a bounded `timeout` event. Clients should reconnect by fetching the canonical job resource; the stream is a latency optimization, not the source of record.

### Canned Responses And Audit Export

Review canned response usage counts and deactivate stale text instead of deleting history. Shortcut uniqueness is tenant-scoped. Audit export requires `metrics:read`, supports bounded pages, and must be written to an access-controlled retention destination by the caller.

When retention enforcement removes old audit rows, it first creates a durable
`audit_archives` manifest and verifies its SHA-256 digest in the same transaction.
Use `GET /api/audit-archives` to inventory manifests and the detail endpoint to
retrieve an export. Run `python scripts/verify_audit_chain.py --db PATH` during
backup/restore drills; the verifier combines archive and hot rows so cold-tier
retention does not hide a broken hash link. A data-subject deletion must not
rewrite audit manifests: customer conversation data is erased, while audit
evidence remains under the independent `audit_events` retention policy. Check
the returned deletion counts (`audit_events=0`, `audit_archives=0`) when
reconciling a request.

### Audit Evidence Operations (SEC-005)

High-risk security changes (API-key issue/revoke, DSR lifecycle, member
invite/role/deactivate, retention/SLA policy, webhook register/delete) are
persisted **in the same transaction** as their audit evidence via
`audit_high_risk()`: append event + read the chain tail + insert an
`audit_anchors` frontier tip together commit or together roll back. If audit
persistence fails, the whole mutation rolls back and the caller answers
`503 code="audit_unavailable"` with `Retry-After: 30` — never a mutation
without evidence. Verify the twelve `HIGH_RISK_EVENT_TYPES` are unchanged
(`app/audit_gap.py`) when adding new dangerous operations.

Every housekeeping cycle exports the chain head as a signed claim to the WORM
/object-lock store (`DiskWormStore`); claims are write-once JSON documents with
a content-hash journal. Treat the WORM directory as a second immutable backup:
never delete or hand-edit claim files in place, and when restoring a WORM that
was lost, restore the whole directory from backup rather than re-signing
apparent "missing" claims — the journal treats object loss as tamper.

Verifying the complete evidence set during a drill or incident:

```bash
python scripts/verify_audit_chain.py \
    --db PATH \
    --environment <env> \
    --worm-dir <worm_dir> \
    --trusted-kids <kid[,kid...]>
```

- exit `0` = chain intact (`audit chain intact` + `DB anchors match` + WORM
  claims signed); `1` = TAMPER with the exact failing layer on stderr
  (`hash mismatch` / `missing DB anchor` / `!= recomputed` / `prev_hash
  mismatch` / `duplicate audit sequence` / `not in the trusted set` / WORM
  availability); `2` = database not found.
- `--worm-dir` omitted means WORM is intentionally not checked (reported);
  pointing it at a path that is a regular file, lacks claims, or is unreachable
  surfaces the store's own exception as a tamper signal.
- After KMS key rotation, keep historical claims and update `trusted_kids` to
  the new kid so old anchors stay verifiable; claims signed by a kid outside
  the list are rejected until the runbook updates the list.
- An operator-facing view is available at `GET /api/admin/audit/anchors/verify`
  (fresh verification report) and `GET /api/admin/audit/gaps` (any
  best-effort telemetry that could not be persisted).

### Security Governance Gate (SEC-008)

Every release commits a threat-model delta and keeps the quarterly security
drill ledger current; both are machine-checked before a release is allowed
through. The ledger and scripts are the source of truth:

```bash
# CI-friendly check (nil-tolerant: an empty ledger is not a violation)
python scripts/threat_model_gate.py

# Release-time: the release MUST have a delta and named owners/approvers
python scripts/threat_model_gate.py --release <version>

# Controlled environment: latest quarterly drill must be fresher than 90 days
python scripts/threat_model_gate.py --check-today --drill-max-days 90
```

- Exit `2` = ledger missing or malformed JSON; exit `1` = violations listed
  on stderr one per line; exit `0` = clean.
- A delta entry lives in `supplychain/threat-model-deltas.json` and requires
  `release` / `date` / `owner` / `approved_by` (named, never a placeholder
  such as `security@helix.example`, TBD, or `<...>`) plus non-empty `controls`
  and `verification_evidence`. An entry dated in the future fails.
- A drill entry lives in `supplychain/security-drills.json`; `drill_type` must
  be one of `report_intake` / `dependency_vuln` / `key_compromise` /
  `cross_tenant_alarm`, with `started_at` / `owner` / `scenario` /
  `duration_minutes`. A future-dated drill and a drill older than
  `--drill-max-days` (default 90) both fail.
- `SECURITY.md`'s real report channel, on-call owner and test times are only
  confirmed from deployment configuration and private run records; performing
  the actual end-to-end channel drill (SEC-007) stays a deployer
  responsibility — the gate only asserts the ledger is structured, named and
  dated legally.

### Credential Exposure

Remove the affected key from `API_KEYS_JSON`, restart all instances, inspect audit events by credential actor and request ID, issue a replacement, and document scope. Do not log or send the secret in incident chat.

### OIDC Callback Failures

Operator logins run the BFF Authorization Code + PKCE flow with one-time
`auth_transactions`. Repeated callback failures usually mean `BASE_URL` does
not match the externally reachable origin (state binding fails) or the IdP
mismatches the allowed algorithms/claims. The login endpoint reports a single
non-informative external error by design and logs the internal detail; check
the app log for `OIDCFlowError` internal_detail, confirm
`OIDC_ISSUER_URL`/`OIDC_CLIENT_ID`/`OIDC_CLIENT_SECRET`, and that
`/auth/callback` terminates TLS at the same origin as `BASE_URL`. After an IdP
key rotation the JWKS cache refreshes on a kid miss; no restart is required.

### DSR Export Missing Or 501

DSR uploads/exports answer `501 http_error` when `DSR_EXPORT_SECRET` is unset
(fail-closed). Set a long random value on all instances, restart, and confirm
`GET /api/admin/dsr-requests` works before rerunning the export. Exported
objects carry a one-time download token ≤15 min and the object expires ≤24 h;
a 410/404 on download means the token/object expired — regenerate the export.
Reconcile maker-checker state against `data_subject_requests`
(requester ≠ approver ≠ executor, `idempotency_key` unique per tenant).

### Unsafe Response

Accept the conversation manually, preserve request/audit IDs, disable the affected knowledge article or model path, add the case to the regression set, and only re-enable after review.

### Connector Unavailable

Order or CRM `unavailable` must escalate to a human without inventing “not found” or leaking another customer's status. Knowledge open-circuit falls back to built-in FTS. Check audit `tool.executed` codes, conversation `handoff_reason` (`Order connector unavailable` / `CRM connector unavailable`), and whether a second lookup after the breaker opens still calls the inner system (it must not). Restore the remote service, then wait for the breaker recovery window; do not lower `failure_threshold` to hide an outage. Re-run `tests/test_connector_degradation.py` and `python scripts/evaluate.py` before re-enabling a new HTTP connector.

### Webhook Delivery Failures

Inspect `GET /api/webhooks/deliveries` for `pending` / `sending` / `dead`. A growing `dead` count usually means the consumer is returning 4xx or remaining 5xx past `max_attempts`. Confirm the consumer verifies `X-Helix-Signature` over `timestamp.body` and deduplicates on `event_id`. A row stuck in `sending` is reclaimed after the sending lease (60s); do not delete it. Transient failures return to `pending` with `next_attempt_at` backoff — do not restart the worker just to flush retries. After fixing the consumer, emit a new event or wait for the next `WEBHOOK_DELIVERY_INTERVAL_SECONDS` tick.

### Performance Gate Failing (Browser Layer)

`python scripts/performance_gate.py --base-url URL` reports `FAIL: <key>:
<value> exceeds budget <limit>` when a Core Web Vitals budget breaches. Follow the
metric-to-cause table in `docs/PERF_NOTES.md` §43.6 before changing any budget —
budgets are the contract; a documented measurement override needs an explicit
changelog entry.

- **`lcp_desktop_ms` / `cls_desktop` breaching**: first check web-side twins
  (`lcp_ms` / `cls`). Web and desktop rising together is a system-load signal
  (background browser processes, memory pressure), not an app regression —
  re-run on a clean DB and an idle machine before touching anything. Only a
  single-track rise (desktop-only, web flat) points at the Tauri shell path:
  splash hand-off, island mount, or sidecar asset staleness.
- **`heap_growth_mb` / `detail_leak_mb` unmeasurable**: the gate reports this
  when `PERF_PRECISE_MEMORY` is off (Chromium quantizes `performance.memory`
  to a fixed 10 MB) or when the launch flag is missing. CI does not set the
  variable by design; a local run needs `PERF_PRECISE_MEMORY=1`.
- **`detail_leak_mb` climbing**: a leak probe failure means detail
  open/close retains heap across cycles. Before debugging the renderer,
  confirm the probe itself worked: it needs a clickable queue row (the INP
  seed) and runs in a separate browser session with `--expose-gc`. A row-less
  page reports "budget went unenforced", not a number.
- **`inp_ms` / `inp_desktop_ms` = 0.0**: the probe found no interaction
  events — the click target (`.conversation-row button.conversation-item`)
  was missing or the seed failed. Seed the queue (the gate does this via
  `POST /api/conversations`) and re-run; the gate refuses to read 0 as a
  pass.
- **Desktop LCP persistently near 1000 ms**: re-run with `PERF_PRECISE_MEMORY=1`
  and the machine idle. Historical local spread is 860–984 ms against a
  1000 ms budget; values past ~1.1 s on a clean DB warrant a thread-island
  inspection rather than a budget raise.

The static byte layer (`operator_js_bytes` / `operator_css_bytes` /
`widget_js_bytes`, no browser needed) fails on bundle growth: check that the
Vite `dist/` was rebuilt (`npx vite build`, `emptyOutDir` clears stale chunks)
and that no unminified vendor blob was added. `--update` rewrites
`artifacts/performance-baseline.json` — only after approving a real budget
change, never to silence a failing gate.

## Deployment Topology And Recovery (Phase 42)

### Process Roles (42.1)

`PROCESS_ROLE=web|worker|all` splits the stateless API tier from the turn
worker. Production `web` instances never run the turn worker or housekeeping;
`worker` instances are converged behind the internal network at the ingress
layer (the application still serves its endpoints for health/diagnostics).
The role is visible in `/health/ready` and `/api/admin/diagnostics`
(`process_role`, `runs_turn_worker`).

### Migrations As A Release Job (42.2)

Application startup does not have to own schema changes. Set
`DATABASE_AUTO_MIGRATE=false` on web/worker processes and run migrations as a
dedicated release step:

```bash
python scripts/run_migrations.py --verify-only   # no-DDL readiness check
python scripts/run_migrations.py                 # apply (idempotent)
```

With auto-migrate disabled, startup verifies the applied versions against the
registry and fails fast on a stale or unmigrated database instead of
executing DDL. This enables a least-privilege split:

- **app role** (`web`/`worker`): SELECT/INSERT/UPDATE/DELETE on application
  tables — no CREATE/ALTER/DROP;
- **migrate role** (release job only): DDL, used exactly once per release.

Schema changes follow the expand/migrate/contract discipline: `expand` adds
tables/columns/indexes without touching existing readers/writers, `migrate`
backfills data idempotently, and `contract` removes schema only after every
deployed version stopped using it. From migration v33 on each step declares
its phase, and `scripts/migration_gate.py` enforces the rules in CI
(contract-without-expand and unphased new migrations fail the gate). Rolling
N/N+1 compatibility means: deploy expand+migrate first, ship code that uses
the new shape, and contract in a later release.

### PostgreSQL TLS And Pooling

Always connect to PostgreSQL over TLS: put `sslmode=require` (or
`verify-full` with a CA bundle) in `DATABASE_URL`. Terminate client
connections on a pooler/proxy (e.g. PgBouncer in transaction mode) sized to
`DATABASE_POOL_SIZE × instance count`; the application opens few connections
per process by design.

### Point-In-Time Recovery (PITR)

Recovery objectives: **RTO ≤ 30 minutes**, **RPO ≤ 15 minutes** (or an
approved SLO update). Take timestamped backups at least every 15 minutes so
a restore-to-point never exceeds the RPO window:

```bash
python scripts/run_pitr_drill.py          # scripted evidence run
```

The drill seeds a full state (conversation, queued turn job, scanned
attachment, anchored audit trail, deletion tombstone), takes T0/T1 backups,
restores T1 into an isolated environment, and verifies: audit chain + WORM
anchors intact, zero loss inside the window, post-T1 writes excluded,
queued job / attachment manifest / tombstone all consistent, and the startup
tombstone re-application path clean. The measured wall time is recorded
against the RTO budget in `supplychain/pitr-drills.json`.

### Redis Failover

PostgreSQL is the job source of truth; Redis holds only rebuildable dispatch
state. After a failover or flush, one `recover()` pass re-pushes every
non-terminal job from the database exactly once (lease fencing prevents
stale workers from double-completing); see
`scripts/redis_failure_drill.py` for the four-class failure contract and
`tests/test_redis_queue.py::test_flush_rebuilds_from_database_exactly_once`
for the flush-rebuild invariant.

### Row-Level Tenant Security (43.2 contract a)

Defence in depth under the application-layer tenant filters: 18 core
customer-data tables carry a PostgreSQL RLS policy comparing each row's
`tenant_id` against the transaction-scoped `app.tenant_id` GUC. The GUC is
bound from the authenticated credential (never request input) at
request start, dies with every commit/rollback (pool reuse starts fail
closed), and its absence makes reads return zero rows and writes violate
`WITH CHECK`.

Enablement order for production:

1. Install policies as the owner/migrate role:
   `python scripts/run_migrations.py` followed by
   `python scripts/run_rls_drill.py` on scratch first;
2. Provision a least-privilege app role (SELECT/INSERT/UPDATE/DELETE +
   sequence USAGE, **no BYPASSRLS**) and point `DATABASE_URL` at it;
3. Set `DATABASE_RLS_ENABLED=1` and roll web/worker instances.

Cross-tenant system work (queue claiming, housekeeping, schema init) runs
under an audited maintenance scope and therefore needs the exempt
owner/BYPASSRLS role — keep that role off the application processes.
Rollback is flipping `DATABASE_RLS_ENABLED=0`; the stored policies are
harmless when unenforced. The live drill evidence lands in
`supplychain/rls-drills.json` and is governed by `threat_model_gate.py
--check-today` (`automated_rls`). See ADR-015.

## AI Cost Attribution And Drift Monitoring

Every model inference (triage, language, copilot, summaries) records prompt/
completion tokens and USD cost into `inference_costs` (migration v44) with a
daily `tenant_cost_daily` rollup. Four admin endpoints (`admin:manage`)
expose it:

- `GET /api/analytics/costs/daily?start_date=&end_date=` — cumulative summary
- `GET /api/analytics/costs/by_agent?date=` and `/by_prompt?date=` — breakdowns
- `GET /api/analytics/costs/anomaly` — current day vs the 7-day baseline
  (`anomaly` fires at 2x by default; rows without vendor pricing carry
  `cost_usd = NULL` and count toward tokens only)

The admin island's cost dashboard card renders all four; the anomaly readout
is the same evaluation the drift monitor applies.

**Drift monitoring** (default off; `DRIFT_ENABLED=1` to enable) runs an hourly
sweep comparing per-tenant signals against thresholds and, on any breach,
clears that tenant's canary prompt versions back to draft and audits
`ai.drift_canary_stopped` (active versions are never touched — rolling back
a retired version stays an operator decision):

| Signal | Threshold | Source |
| --- | --- | --- |
| escalation / negative-feedback rate | `DRIFT_MAX_ESCALATION_RATE` / `DRIFT_MAX_NEGATIVE_RATE` | quality buckets |
| model / tool denials | `DRIFT_MAX_MODEL_DENIALS` / `DRIFT_MAX_TOOL_DENIALS` | audit events |
| cost factor (today vs baseline) | `DRIFT_MAX_COST_FACTOR` (default 2.0) | `tenant_cost_daily` |
| stale-citation share | `DRIFT_MAX_STALE_CITATION_RATE` (default 0.2) | messages + knowledge articles |

Small samples stay silent (`DRIFT_MIN_TURNS`); each signal can be disabled
individually by unsetting it. Operator response to a breach: inspect the
audit payload's `signals` array, decide whether the canary candidate stays
retired, and re-run the eval gate before re-entering canary routing.

## Shadow Traffic And Multi-Cell Operations

**Shadow traffic** (default off; `SHADOW_TRAFFIC_ENABLED=1`,
`SHADOW_TRAFFIC_SAMPLE_RATE` to tune) replays sampled v1 read requests to the
v2 endpoint configured by `SHADOW_TRAFFIC_BASE_URL`, storing field-level
comparisons in `shadow_traffic_comparisons`. The hourly health monitor
evaluates a rolling 24-hour window and logs an alert when mismatch rate or
latency regression breaches its thresholds — investigate the comparison rows
before promoting v2.

**Multi-cell** (`CELL_REGISTRY_JSON`): when a registry is configured, the
process starts per-peer replication workers (internal-authenticated
`POST /api/internal/replication/apply`) and periodic cell health checks as
background tasks. **Regional failover** is an operator runbook, not an
automatic behavior: `python scripts/run_region_failover.py --target-region
<r> [--dry-run]` verifies target-cell health, enforces data residency, and
publishes a signed new control-plane snapshot; `--dry-run` validates without
publishing. See `ROADMAP_2_X.md` §42-43 for the gate evidence.

## AI Governance Operations (2.5-2.7)

The governance plane is operable from the admin island's 治理操作台 card
and the governance API (all `admin:manage`):

- **Pending approvals** (`GET /api/admin/governance/approvals?status=pending`):
  maker-checker requests for `tool_enablement` and other governance
  subjects. Decide via the card's 批准/拒绝 buttons or
  `POST /api/admin/governance/approvals/{id}/decide` — the requester
  cannot be the approver (self-approval → 409). A high-risk tool stays
  inoperable until its approval is granted.
- **Online feedback review queue** (`GET /api/admin/governance/feedback?status=pending_review`):
  customer negative ratings auto-stage the exchange here, redacted at
  ingest. Accept or reject via the card's 接受/拒绝 buttons or
  `POST /api/admin/governance/feedback/{id}/review`.
- **Eval dataset promotion** (`POST /api/admin/governance/datasets/promote-feedback`):
  folds accepted feedback rows into the next version of a named dataset;
  any pending/rejected id in the batch aborts with 409.
- **Capability tokens**: set `CAPABILITY_SECRET` (>= 32 bytes) to turn on
  token verification for gateway-mediated tool calls; the copilot
  knowledge-draft endpoint mints a short-TTL token per call. Without the
  secret, presented tokens fail closed (`token_unsupported`) and the
  process logs `capability_secret.unset` at boot.

## Routine Work

- Daily: review SLA breaches, tool failures, negative feedback, and escalations.
- Weekly: sample grounded answers and human handoff packages.
- Monthly: rotate non-SSO credentials, test backups, review inactive knowledge.
- Quarterly: restore drill, load test, prompt-injection suite, and incident exercise.

## Disaster-Recovery Drills

The quarterly restore drill and load test are scripted so they can be run
reproducibly; they double as regression coverage in the CI gate.

### Backup/Restore Drill (`tests/test_drills.py`)

- **SQLite online-backup drill** runs unconditionally: a background writer
  keeps inserting messages while `scripts/backup.py` takes an online snapshot,
  and the snapshot must pass `PRAGMA integrity_check` and stay internally
  coherent (every conversation's `message_count`/`preview` matches its rows).
  It also proves a restore reproduces the exact seeded rows and that a
  tampered backup is refused by checksum before it can overwrite the target.

  ```bash
  python -m unittest tests.test_drills.BackupConsistencyTests -v
  ```

- **PostgreSQL production restore drill** destroys the target database's
  `public` schema, dumps it first with `pg_dump`, restores with `psql`, and
  verifies the seeded rows came back.  It is skipped unless
  `HELIX_PG_INTEGRATION=1` and `DATABASE_URL` are set.

  ```bash
  HELIX_PG_INTEGRATION=1 \
  DATABASE_URL="host=localhost port=5432 user=postgres password=<...> dbname=<scratch>" \
  python -m unittest tests.test_drills.PostgresRestoreDrillTests -v
  ```

  > **Danger:** this drill executes `DROP SCHEMA public CASCADE`.  Point
  > `DATABASE_URL` only at a disposable scratch database, never at production.
  > `PG_BIN` (default `D:/PostgreSQL/18/bin`) must contain `pg_dump` and `psql`.

The plain round-trip in `tests/test_concurrency.py` only proves a backup
opens; the drill above proves the two properties that matter before trusting a
backup in an incident — a *consistent* snapshot under live writes, and a
restore that *recovers data* rather than recreating empty tables.

### Load Test (`scripts/load_test.py`)

Simulates the real operator workload — create conversation, send a message
(routed through the orchestrator), and optionally await the resulting
asynchronous turn job — while measuring latency and throughput.  Needs a
running instance with `AUTH_MODE=demo`.

```bash
python scripts/load_test.py --base-url http://127.0.0.1:8000 \
    --concurrency 10 --duration 60 --poll-turn-jobs
```

Rate-limited responses (HTTP 429) are reported separately from genuine
failures, so the success rate reflects real errors rather than the
application's own throttle.  Running many workers against one demo tenant
saturates that tenant's rate-limit bucket; raise `RATE_LIMIT_PER_MINUTE` on
the server for a saturation run, or spread workers with `--tenants` under
`AUTH_MODE=api_key`, where each tenant maps to its own API key.  The command
exits non-zero if the genuine-failure rate exceeds 2%.
