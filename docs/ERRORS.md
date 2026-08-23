# Error Contract (RFC 9457 Problem Details)

Every API error response is a JSON Problem Details document (RFC 9457). The
legacy top-level `detail` field is preserved for one minor release so existing
clients keep working; new integrations should read `title`/`status`/
`instance`/`code` instead.

## Envelope

```json
{
  "type": "urn:helix:error:not_found",
  "title": "Not Found",
  "status": 404,
  "detail": "Conversation not found",
  "instance": "/api/conversations/conv-123",
  "request_id": "req_9f3c...",
  "code": "not_found"
}
```

| Field | Meaning |
|-------|---------|
| `type` | Stable machine-readable error URI, `urn:helix:error:<code>`. `about:blank` is never used — every error has a code. |
| `title` | Human-readable summary of the error class (RFC 9110 reason phrase). |
| `status` | HTTP status code, echoed. |
| `detail` | Human-readable explanation; also the legacy field. |
| `instance` | The request path that failed. |
| `request_id` | Correlation id from `X-Request-Id`; matches the audit trail. |
| `code` | Stable slug for client dispatch (same suffix as `type`). |
| `errors` | Present only for 422 validation errors: per-field `{type, loc, msg, ctx}` entries. |

## Error Catalog

### 400 Bad Request

| code | meaning | retryable | action |
|------|---------|-----------|--------|
| `bad_request` | Malformed request, invalid idempotency key, conflicting pagination params | No | Fix the request before retrying. |
| `validation_error` (422) | Request body failed schema validation; `errors` lists each field | No | Inspect `errors[].loc` and `errors[].msg`, fix the payload. |

### 401 Unauthorized

| code | meaning | retryable | action |
|------|---------|-----------|--------|
| `unauthorized` | Missing or invalid `X-API-Key` | No | Obtain a valid API key. |

### 403 Forbidden

| code | meaning | retryable | action |
|------|---------|-----------|--------|
| `forbidden` | Key is valid but lacks the required permission, or requests a tenant the key cannot access | No | Use a key with the required role/permission for that tenant. |

### 404 Not Found

| code | meaning | retryable | action |
|------|---------|-----------|--------|
| `not_found` | Resource does not exist in this tenant | No | Verify the id and tenant. |

### 409 Conflict

| code | meaning | retryable | action |
|------|---------|-----------|--------|
| `conflict` | Duplicate resource (e.g. canned-response shortcut), general conflict | No | Change the payload or reconcile first. |
| `invalid_transition` | Conversation/knowledge state machine rejected the transition (e.g. publish an already-published article) | No | Move the resource through a legal sequence. |
| `idempotency_conflict` | Same idempotency key replayed with a different payload | No | Reuse the exact original payload or a fresh key. |
| `turn_in_progress` | A turn for the conversation is still processing; `Retry-After: 1` | Yes | Retry after `Retry-After`. |

### 429 Too Many Requests

| code | meaning | retryable | action |
|------|---------|-----------|--------|
| `rate_limited` | Per-key rate limit exceeded | Yes | Honor `Retry-After` and back off. |

### 5xx

| code | meaning | retryable | action |
|------|---------|-----------|--------|
| `http_error` | Unexpected server error | Yes | Check `request_id` in logs/metrics; retry with backoff. |
| `queue_unavailable` (503) | Task queue backend unreachable on a fail-closed deployment; the message was **not** accepted (`Retry-After: 30`) | Yes | Honor `Retry-After`; restore the queue before resubmitting. |
| `http_error` (501) | DSR export/delete not configured (`DSR_EXPORT_SECRET` unset) — fails closed on purpose | No | Configure the secret and create a fresh request. |

## M0 (停止线) 补充

### OIDC 登录失败(`/auth/login`、`/auth/callback`)

- 失败统一走 `HTTPException`,映射为标准 `code`:`400 → bad_request`(state/code 无效、事务过期、claims 被拒)、`502 → http_error`(发现端点或 token 端点不可达)。
- 响应体绝不包含 IdP 返回的原始错误、token 片段或成员详情;只返回固定 `public_message` 与日志侧 `internal_detail`。
- 会话未建立时不设 cookie;重放已消费的 `state` 会被一次性事务拒绝(`bad_request`)。

### DSR(`/api/data-subject-requests*`)

- `400 bad_request`:非法请求类型、幂等键冲突、非法状态迁移(如重复 approve)。
- `404 not_found`:请求 id 不存在或不属于当前租户。
- `403 forbidden`:当前角色缺少 `privacy:request` / `privacy:approve` / `privacy:execute`。
- `501 http_error`:导出对象加密未配置(`DSR_EXPORT_SECRET` 为空),导出 fail-closed。
- maker-checker:创建者不能 approve 自己的请求;approve 与 execute 分离,全链路写入审计链。

## Compatibility

- The legacy `{"detail": "..."}` shape was replaced by the envelope above in
  1.1.0. The `detail` key remains in every body through at least 1.2.0
  (see `docs/API_POLICY.md`).
- Consumers must not depend on the exact wording of `detail`; dispatch on
  `code` (or `status`) instead.
