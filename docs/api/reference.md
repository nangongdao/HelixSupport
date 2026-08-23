# Helix Support API Reference

Version: `1.3.0`

This reference is generated from the OpenAPI contract snapshot (`api/openapi.json`) by `scripts/api_docs.py`. The error contract is documented in [ERRORS.md](../ERRORS.md); versioning and deprecation policy in [API_POLICY.md](../API_POLICY.md).

## Admin

### GET `/api/admin/agent-groups`

**List agent groups (skills + capacity)**

List agent groups (skills + capacity). Requires: admin:manage.

*Tags:* `admin`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  array of:
    {
      id: string (required)
      tenant_id: string (required)
      name: string (required)
      skills: array
      capacity: integer (required)
      created_at: string (required)
    }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### POST `/api/admin/agent-groups`

**Create an agent group**

Create an agent group. Requires: admin:manage.

*Tags:* `admin`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Request body**

`application/json`

{
  name: string (required)
  skills: array
  capacity: integer
}

**Responses**

- `201` Successful Response

  `application/json`

  {
    id: string (required)
    tenant_id: string (required)
    name: string (required)
    skills: array
    capacity: integer (required)
    created_at: string (required)
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### DELETE `/api/admin/agent-groups/{group_id}`

**Delete an agent group**

Delete an agent group. Requires: admin:manage.

*Tags:* `admin`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `group_id` | path | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `204` Successful Response

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### POST `/api/admin/agent-groups/{group_id}/agents`

**Add an agent to a group**

Add an agent to a group. Requires: admin:manage.

*Tags:* `admin`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `group_id` | path | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Request body**

`application/json`

object

**Responses**

- `201` Successful Response

  `application/json`

  {
    group_id: string (required)
    tenant_id: string (required)
    actor_id: string (required)
    added_at: string (required)
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### DELETE `/api/admin/agent-groups/{group_id}/agents/{actor_id}`

**Remove an agent from a group**

Remove an agent from a group. Requires: admin:manage.

*Tags:* `admin`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `group_id` | path | yes |  |
| `actor_id` | path | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `204` Successful Response

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### GET `/api/admin/csat-summary`

**Aggregate answered CSAT surveys; days only bounds the per-day trend (readouts are all-history)**

Aggregate answered CSAT surveys for the admin「评分汇总」card.

*Tags:* `admin`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `days` | query | no |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  {
    total: integer (required)
    avg_rating: number (required)
    positive_rate: number (required)
    per_day: array (required)
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### GET `/api/admin/diagnostics`

**Support diagnostics bundle (version/config/queue/audit head)**

Support diagnostics bundle (Phase 30.2).

Version, redacted config summary, queue state, worker snapshot,
recent terminal failures, and the audit chain head — everything an
on-call engineer needs to triage, without secrets.

*Tags:* `admin`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  object

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### POST `/api/admin/keys/{credential_id}/revoke`

**Revoke an API key by credential id**

Revoke an API key by its credential id (Phase 28.2).

The credential id is the sha256[:12] of the key, exposed via
``GET /api/me`` (``credential_id``). Revoking takes effect
immediately in-process and is persisted so it survives restarts.

*Tags:* `admin`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `credential_id` | path | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  object

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### GET `/api/admin/routing-rules`

**List routing rules**

List routing rules. Requires: admin:manage.

*Tags:* `admin`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  array of:
    {
      id: string (required)
      tenant_id: string (required)
      intent: object
      label: object
      channel: object
      group_id: string (required)
      priority: integer (required)
      created_at: string (required)
    }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### POST `/api/admin/routing-rules`

**Create a routing rule (intent/label/channel -> group)**

Create a routing rule (intent/label/channel -> group). Requires: admin:manage.

*Tags:* `admin`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Request body**

`application/json`

{
  intent: object
  label: object
  channel: object
  group_id: string (required)
  priority: integer
}

**Responses**

- `201` Successful Response

  `application/json`

  {
    id: string (required)
    tenant_id: string (required)
    intent: object
    label: object
    channel: object
    group_id: string (required)
    priority: integer (required)
    created_at: string (required)
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### DELETE `/api/admin/routing-rules/{rule_id}`

**Delete a routing rule**

Delete a routing rule. Requires: admin:manage.

*Tags:* `admin`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `rule_id` | path | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `204` Successful Response

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### GET `/api/admin/sla-policies`

**List SLA policies (tenant/priority/channel limits)**

List SLA policies (tenant/priority/channel limits). Requires: admin:manage.

*Tags:* `admin`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  array of:
    {
      id: string (required)
      tenant_id: object
      priority: object
      channel: object
      first_response_minutes: integer (required)
      resolve_minutes: integer (required)
      created_at: string (required)
      updated_at: string (required)
    }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### PUT `/api/admin/sla-policies`

**Configure an SLA policy**

Configure an SLA policy. Requires: admin:manage.

*Tags:* `admin`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Request body**

`application/json`

{
  tenant_id: object
  priority: object
  channel: object
  first_response_minutes: integer (required)
  resolve_minutes: integer (required)
}

**Responses**

- `200` Successful Response

  `application/json`

  {
    id: string (required)
    tenant_id: object
    priority: object
    channel: object
    first_response_minutes: integer (required)
    resolve_minutes: integer (required)
    created_at: string (required)
    updated_at: string (required)
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### POST `/api/admin/tenants`

**Provision a tenant (idempotent)**

Idempotently provision a tenant with default policy (Phase 22.1).

Creating the same tenant id twice returns the tenant (idempotent);
quota fields given here are applied on both calls.

*Tags:* `admin`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Request body**

`application/json`

{
  tenant_id: string (required)
  name: string (required)
  allowed_models: object
  daily_turn_budget: object
  conversation_quota: object
  storage_quota_bytes: object
}

**Responses**

- `201` Successful Response

  `application/json`

  {
    tenant_id: string (required)
    name: string (required)
    allowed_models: object
    daily_turn_budget: object
    conversation_quota: object
    storage_quota_bytes: object
    created_at: string (required)
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### POST `/api/admin/tenants/{tenant_id}/members`

**Invite a tenant member**

Invite a member to a tenant (Phase 22.2). Idempotent per member.

*Tags:* `admin`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `tenant_id` | path | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Request body**

`application/json`

{
  actor_id: string (required)
  role: string (required)
}

**Responses**

- `201` Successful Response

  `application/json`

  {
    id: string (required)
    tenant_id: string (required)
    actor_id: string (required)
    role: string (required)
    status: string (required)
    invited_by: object
    invited_at: object
    updated_at: string (required)
    last_login_at: object
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### GET `/api/admin/tenants/{tenant_id}/members`

**List tenant members**

List tenant members. Requires: tenant:manage.

*Tags:* `admin`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `tenant_id` | path | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  array of:
    {
      id: string (required)
      tenant_id: string (required)
      actor_id: string (required)
      role: string (required)
      status: string (required)
      invited_by: object
      invited_at: object
      updated_at: string (required)
      last_login_at: object
    }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### PATCH `/api/admin/tenants/{tenant_id}/members/{actor_id}`

**Change a member's role**

Change a member's role. Requires: tenant:manage.

*Tags:* `admin`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `tenant_id` | path | yes |  |
| `actor_id` | path | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Request body**

`application/json`

{
  role: string (required)
}

**Responses**

- `200` Successful Response

  `application/json`

  {
    id: string (required)
    tenant_id: string (required)
    actor_id: string (required)
    role: string (required)
    status: string (required)
    invited_by: object
    invited_at: object
    updated_at: string (required)
    last_login_at: object
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### POST `/api/admin/tenants/{tenant_id}/members/{actor_id}/deactivate`

**Deactivate a tenant member**

Deactivate a member; history and audit events are retained (22.2).

*Tags:* `admin`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `tenant_id` | path | yes |  |
| `actor_id` | path | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  {
    id: string (required)
    tenant_id: string (required)
    actor_id: string (required)
    role: string (required)
    status: string (required)
    invited_by: object
    invited_at: object
    updated_at: string (required)
    last_login_at: object
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### GET `/api/admin/tenants/{tenant_id}/model-policy`

**Read tenant model policy**

Read tenant model policy. Requires: admin:manage.

*Tags:* `admin`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `tenant_id` | path | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  {
    tenant_id: string (required)
    allowed_models: object
    daily_turn_budget: object
    daily_turn_count: integer
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### PUT `/api/admin/tenants/{tenant_id}/model-policy`

**Set tenant model policy**

Set tenant model policy. Requires: admin:manage.

*Tags:* `admin`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `tenant_id` | path | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Request body**

`application/json`

{
  allowed_models: object
  daily_turn_budget: object
}

**Responses**

- `200` Successful Response

  `application/json`

  {
    tenant_id: string (required)
    allowed_models: object
    daily_turn_budget: object
    daily_turn_count: integer
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### GET `/api/admin/tenants/{tenant_id}/quota`

**Read tenant quota**

Read tenant quota. Requires: tenant:manage.

*Tags:* `admin`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `tenant_id` | path | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  {
    tenant_id: string (required)
    name: string (required)
    allowed_models: object
    daily_turn_budget: object
    conversation_quota: object
    storage_quota_bytes: object
    created_at: string (required)
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### PUT `/api/admin/tenants/{tenant_id}/quota`

**Update tenant quota**

Update tenant quota. Requires: tenant:manage.

*Tags:* `admin`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `tenant_id` | path | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Request body**

`application/json`

{
  conversation_quota: object
  storage_quota_bytes: object
}

**Responses**

- `200` Successful Response

  `application/json`

  {
    tenant_id: string (required)
    name: string (required)
    allowed_models: object
    daily_turn_budget: object
    conversation_quota: object
    storage_quota_bytes: object
    created_at: string (required)
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### GET `/api/admin/usage`

**Export raw daily tenant usage**

Raw daily usage for billing (Phase 22.4).

``tenant_id`` defaults to the caller's tenant; a system admin may
pass any tenant id.

*Tags:* `admin`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `tenant_id` | query | no |  |
| `since` | query | no |  |
| `until` | query | no |  |
| `limit` | query | no |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  array of:
    {
      tenant_id: string (required)
      date: string (required)
      turn_count: integer (required)
      conversation_count: integer (required)
      message_count: integer (required)
    }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### POST `/api/retention/enforce`

**Run retention enforcement**

Run retention enforcement. Requires: admin:manage.

*Tags:* `admin`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  object

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### GET `/api/retention/policies`

**List retention policies**

List retention policies. Requires: admin:manage.

*Tags:* `admin`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  array of object

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### PUT `/api/retention/policies/{data_type}`

**Set a retention policy**

Set a retention policy. Requires: admin:manage.

*Tags:* `admin`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `data_type` | path | yes |  |
| `retention_days` | query | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  object

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

## Attachments

### POST `/api/attachments`

**Upload an attachment to a conversation (validated + scanned)**

Upload an attachment to a conversation (validated + scanned). Requires: operator:act.

*Tags:* `attachments`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Request body**

`multipart/form-data`

{
  conversation_id: string (required)
  file: string (required)
}

**Responses**

- `201` Successful Response

  `application/json`

  {
    id: string (required)
    tenant_id: string (required)
    conversation_id: string (required)
    message_id: object
    filename: string (required)
    content_type: string (required)
    size_bytes: integer (required)
    uploader: string (required)
    status: string (required)
    scanned: boolean (required)
    verdict: string (required)
    created_at: string (required)
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### GET `/api/attachments`

**List a conversation's stored attachments**

List a conversation's stored attachments. Requires: conversation:read.

*Tags:* `attachments`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `conversation_id` | query | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  array of:
    {
      id: string (required)
      tenant_id: string (required)
      conversation_id: string (required)
      message_id: object
      filename: string (required)
      content_type: string (required)
      size_bytes: integer (required)
      uploader: string (required)
      status: string (required)
      scanned: boolean (required)
      verdict: string (required)
      created_at: string (required)
    }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### GET `/api/attachments/{attachment_id}`

**Attachment metadata**

Attachment metadata. Requires: conversation:read.

*Tags:* `attachments`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `attachment_id` | path | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  {
    id: string (required)
    tenant_id: string (required)
    conversation_id: string (required)
    message_id: object
    filename: string (required)
    content_type: string (required)
    size_bytes: integer (required)
    uploader: string (required)
    status: string (required)
    scanned: boolean (required)
    verdict: string (required)
    created_at: string (required)
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### DELETE `/api/attachments/{attachment_id}`

**Delete an attachment (removes the file and frees quota)**

Delete an attachment (removes the file and frees quota). Requires: operator:act.

*Tags:* `attachments`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `attachment_id` | path | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  object

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### GET `/api/attachments/{attachment_id}/download`

**Force-download an attachment (Content-Disposition: attachment)**

Force-download an attachment (Content-Disposition: attachment). Requires: conversation:read.

*Tags:* `attachments`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `attachment_id` | path | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`



- `422` Validation Error

  `application/json`

  {
    detail: array
  }

## Audit

### GET `/api/audit-archives`

**List audit archives**

List audit archives. Requires: metrics:read.

*Tags:* `audit`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `limit` | query | no |  |
| `offset` | query | no |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  array of:
    {
      id: string (required)
      cutoff: string (required)
      event_count: integer (required)
      first_seq: integer (required)
      last_seq: integer (required)
      first_event_hash: string (required)
      last_event_hash: string (required)
      content_sha256: string (required)
      created_at: string (required)
    }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### GET `/api/audit-archives/{archive_id}`

**Get audit archive**

Get audit archive. Requires: metrics:read.

*Tags:* `audit`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `archive_id` | path | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  {
    id: string (required)
    cutoff: string (required)
    event_count: integer (required)
    first_seq: integer (required)
    last_seq: integer (required)
    first_event_hash: string (required)
    last_event_hash: string (required)
    content_sha256: string (required)
    created_at: string (required)
    events: array (required)
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### GET `/api/audit-events`

**List audit events**

List audit events. Requires: metrics:read.

*Tags:* `audit`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `conversation_id` | query | no |  |
| `event_type` | query | no |  |
| `since` | query | no |  |
| `until` | query | no |  |
| `limit` | query | no |  |
| `offset` | query | no |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  array of:
    {
      id: string (required)
      request_id: object
      actor: string (required)
      event_type: string (required)
      payload: object (required)
      created_at: string (required)
    }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

## Auth

### GET `/api/me`

**Current user profile**

Current user profile. Requires: any authenticated key.

*Tags:* `auth`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  {
    tenant_id: string (required)
    actor_id: string (required)
    role: string (required)
    permissions: array (required)
    local_drafts_enabled: boolean
    local_draft_ttl_minutes: integer
    credential_id: string
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### GET `/auth/callback`

**OIDC callback**

OIDC callback. Requires: none (unauthenticated).

*Tags:* `auth`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `code` | query | yes |  |
| `state` | query | yes |  |
| `tenant` | query | no |  |

**Responses**

- `200` Successful Response

  `application/json`



- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### GET `/auth/login`

**OIDC login redirect**

OIDC login redirect. Requires: none (unauthenticated).

*Tags:* `auth`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `tenant` | query | no |  |

**Responses**

- `200` Successful Response

  `application/json`



- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### POST `/auth/logout`

**End the session**

End the session. Requires: any authenticated session.

*Tags:* `auth`

**Responses**

- `200` Successful Response

  `application/json`

  object

### POST `/auth/refresh`

**Refresh the session cookie**

Renew the session cookie (sliding expiry) for an active session.

The BFF holds no server-side session state, so renewal reissues the
cookie with a fresh ``expires_at`` for the same session id, up to the
configured absolute lifetime.  A missing, expired, or over-lifetime
session gets ``401`` and the client must run the login flow again.

*Tags:* `auth`

**Responses**

- `200` Successful Response

  `application/json`

  object

### GET `/auth/session`

**Current session**

Current session. Requires: any authenticated session.

*Tags:* `auth`

**Responses**

- `200` Successful Response

  `application/json`

  object

## Canned-responses

### GET `/api/canned-responses`

**List canned responses**

List canned responses. Requires: conversation:read.

*Tags:* `canned-responses`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `search` | query | no |  |
| `include_inactive` | query | no |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  array of:
    {
      id: string (required)
      tenant_id: string (required)
      title: string (required)
      body: string (required)
      shortcut: object
      tags: array
      active: boolean
      usage_count: integer
      created_by: string (required)
      updated_by: string (required)
      created_at: string (required)
      updated_at: string (required)
    }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### POST `/api/canned-responses`

**Create a canned response**

Create a canned response. Requires: knowledge:write.

*Tags:* `canned-responses`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Request body**

`application/json`

{
  title: string (required)
  body: string (required)
  shortcut: object
  tags: array
}

**Responses**

- `201` Successful Response

  `application/json`

  {
    id: string (required)
    tenant_id: string (required)
    title: string (required)
    body: string (required)
    shortcut: object
    tags: array
    active: boolean
    usage_count: integer
    created_by: string (required)
    updated_by: string (required)
    created_at: string (required)
    updated_at: string (required)
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### PATCH `/api/canned-responses/{response_id}`

**Update a canned response**

Update a canned response. Requires: knowledge:write.

*Tags:* `canned-responses`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `response_id` | path | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Request body**

`application/json`

{
  title: object
  body: object
  shortcut: object
  tags: object
  active: object
}

**Responses**

- `200` Successful Response

  `application/json`

  {
    id: string (required)
    tenant_id: string (required)
    title: string (required)
    body: string (required)
    shortcut: object
    tags: array
    active: boolean
    usage_count: integer
    created_by: string (required)
    updated_by: string (required)
    created_at: string (required)
    updated_at: string (required)
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### POST `/api/canned-responses/{response_id}/use`

**Record canned-response usage**

Record canned-response usage. Requires: operator:act.

*Tags:* `canned-responses`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `response_id` | path | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  {
    id: string (required)
    tenant_id: string (required)
    title: string (required)
    body: string (required)
    shortcut: object
    tags: array
    active: boolean
    usage_count: integer
    created_by: string (required)
    updated_by: string (required)
    created_at: string (required)
    updated_at: string (required)
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

## Channels

### POST `/api/channels/{account_id}/webhook`

**Receive a signed formal-channel customer message**

Authenticate and durably enqueue one external customer message.

*Tags:* `channels`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `account_id` | path | yes |  |
| `X-Helix-Timestamp` | header | no |  |
| `X-Helix-Signature` | header | no |  |

**Request body**

`application/json`

{
  event: string
  message_id: string (required)
  thread_id: string (required)
  customer_id: string (required)
  customer_name: string (required)
  content: string (required)
}

**Responses**

- `202` Successful Response

  `application/json`

  {
    conversation_id: string (required)
    job_id: object
    status: string (required)
    idempotent_replay: boolean (required)
    conversation_created: boolean (required)
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

## Collaboration

### GET `/api/collaborators`

**List tenant actors for the @-mention autocomplete**

Tenant actors for the @-mention autocomplete (backlog M18).

Read-only roster of member actor ids; restricted to operators (the
only roles who act in the workspace) so viewer/auditor cannot
enumerate the roster. The note composer filters out the writer and
matches the trailing ``@token`` client-side.

*Tags:* `collaboration`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  array of:
    {
      actor_id: string (required)
      role: string (required)
    }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### GET `/api/conversations/{conversation_id}/events`

**Supervisor live view: SSE revision stream for a conversation**

Supervisor live view (旁观模式) for an in-progress conversation.

Read-only SSE: emits a ``snapshot`` with the current revision, then a
``conversation`` event whenever the conversation's updated_at or the
latest message seq changes. ``conversation:read`` suffices (no
``operator:act``), so a supervisor can watch without claiming or
touching the conversation.

*Tags:* `collaboration`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `conversation_id` | path | yes |  |
| `timeout` | query | no |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`



- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### GET `/api/conversations/{conversation_id}/threads`

**List internal discussion threads for a conversation**

List internal discussion threads for a conversation. Requires: conversation:read.

*Tags:* `collaboration`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `conversation_id` | path | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  {
    threads: array
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### GET `/api/mentions`

**List my mentions (unread first) with the unread count**

List my mentions (unread first) with the unread count. Requires: conversation:read.

*Tags:* `collaboration`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `unread_only` | query | no |  |
| `limit` | query | no |  |
| `offset` | query | no |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  {
    mentions: array
    unread_count: integer
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### POST `/api/mentions/{mention_id}/read`

**Mark one of my mentions as read (idempotent)**

Mark one of my mentions as read (idempotent). Requires: conversation:read.

*Tags:* `collaboration`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `mention_id` | path | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  {
    id: string (required)
    conversation_id: string (required)
    conversation_customer: string (required)
    channel: string (required)
    mentioned_by: string (required)
    note_id: string (required)
    note_preview: string (required)
    created_at: string (required)
    read_at: object
    unread: boolean
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

## Conversations

### GET `/api/conversation-labels`

**Label catalog with counts**

Label catalog with counts. Requires: conversation:read.

*Tags:* `conversations`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  array of:
    {
      label: string (required)
      conversation_count: integer (required)
    }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### GET `/api/conversations`

**List conversations**

List conversations. Requires: conversation:read.

*Tags:* `conversations`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `status` | query | no |  |
| `search` | query | no |  |
| `label` | query | no |  |
| `priority` | query | no |  |
| `channel` | query | no |  |
| `assigned_to` | query | no |  |
| `claimed_by` | query | no |  |
| `mine` | query | no |  |
| `unassigned` | query | no |  |
| `unclaimed` | query | no |  |
| `sla_breached` | query | no |  |
| `needs_response` | query | no |  |
| `archived` | query | no |  |
| `sort` | query | no |  |
| `limit` | query | no |  |
| `offset` | query | no |  |
| `cursor` | query | no |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  array of:
    {
      id: string (required)
      tenant_id: string (required)
      customer_name: string (required)
      customer_ref: object
      channel: string (required)
      status: string (required)
      intent: object
      assigned_agent: object
      priority: string (required)
      handoff_reason: object
      sla_due_at: object
      last_confidence: object
      version: integer
      created_at: string (required)
      updated_at: string (required)
      resolved_at: object
      preview: object
      message_count: integer
      last_message_at: object
      labels: array
      claimed_by: object
      claimed_at: object
      claim_expires_at: object
      claim_active: boolean
      sla_breached: boolean
      needs_response: boolean
      waiting_since: object
      first_response_at: object
      survey_url: object
      language: object
      ticket_id: object
    }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### POST `/api/conversations`

**Create a conversation**

Create a conversation. Requires: conversation:write.

*Tags:* `conversations`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Request body**

`application/json`

{
  customer_name: string (required)
  customer_ref: object
  channel: string
}

**Responses**

- `201` Successful Response

  `application/json`

  {
    id: string (required)
    tenant_id: string (required)
    customer_name: string (required)
    customer_ref: object
    channel: string (required)
    status: string (required)
    intent: object
    assigned_agent: object
    priority: string (required)
    handoff_reason: object
    sla_due_at: object
    last_confidence: object
    version: integer
    created_at: string (required)
    updated_at: string (required)
    resolved_at: object
    preview: object
    message_count: integer
    last_message_at: object
    labels: array
    claimed_by: object
    claimed_at: object
    claim_expires_at: object
    claim_active: boolean
    sla_breached: boolean
    needs_response: boolean
    waiting_since: object
    first_response_at: object
    survey_url: object
    language: object
    ticket_id: object
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### POST `/api/conversations/bulk-actions`

**Bulk priority/label/claim actions**

Bulk priority/label/claim actions. Requires: operator:act.

*Tags:* `conversations`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Request body**

`application/json`

{
  conversation_ids: array (required)
  action: string (required)
  priority: object
  labels: array
}

**Responses**

- `200` Successful Response

  `application/json`

  {
    requested: integer (required)
    matched: integer (required)
    updated: integer (required)
    unchanged: integer (required)
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### PATCH `/api/conversations/{conversation_id}`

**Update conversation priority**

Update conversation priority. Requires: operator:act.

*Tags:* `conversations`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `conversation_id` | path | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Request body**

`application/json`

{
  priority: string (required)
}

**Responses**

- `200` Successful Response

  `application/json`

  {
    id: string (required)
    tenant_id: string (required)
    customer_name: string (required)
    customer_ref: object
    channel: string (required)
    status: string (required)
    intent: object
    assigned_agent: object
    priority: string (required)
    handoff_reason: object
    sla_due_at: object
    last_confidence: object
    version: integer
    created_at: string (required)
    updated_at: string (required)
    resolved_at: object
    preview: object
    message_count: integer
    last_message_at: object
    labels: array
    claimed_by: object
    claimed_at: object
    claim_expires_at: object
    claim_active: boolean
    sla_breached: boolean
    needs_response: boolean
    waiting_since: object
    first_response_at: object
    survey_url: object
    language: object
    ticket_id: object
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### GET `/api/conversations/{conversation_id}`

**Conversation detail with messages, audit events, and summaries**

Conversation detail with messages, audit events, and summaries. Requires: conversation:read.

*Tags:* `conversations`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `conversation_id` | path | yes |  |
| `message_limit` | query | no |  |
| `message_cursor` | query | no |  |
| `messages_before` | query | no |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  {
    conversation: object (required)
      {
        id: string (required)
        tenant_id: string (required)
        customer_name: string (required)
        customer_ref: object
        channel: string (required)
        status: string (required)
        intent: object
        assigned_agent: object
        priority: string (required)
        handoff_reason: object
        sla_due_at: object
        last_confidence: object
        version: integer
        created_at: string (required)
        updated_at: string (required)
        resolved_at: object
        preview: object
        message_count: integer
        last_message_at: object
        labels: array
        claimed_by: object
        claimed_at: object
        claim_expires_at: object
        claim_active: boolean
        sla_breached: boolean
        needs_response: boolean
        waiting_since: object
        first_response_at: object
        survey_url: object
        language: object
        ticket_id: object
      }
    messages: array (required)
    audit_events: array (required)
    summaries: array
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### POST `/api/conversations/{conversation_id}/accept`

**Accept a conversation into human_active**

Accept a conversation into human_active. Requires: operator:act.

*Tags:* `conversations`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `conversation_id` | path | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  {
    id: string (required)
    tenant_id: string (required)
    customer_name: string (required)
    customer_ref: object
    channel: string (required)
    status: string (required)
    intent: object
    assigned_agent: object
    priority: string (required)
    handoff_reason: object
    sla_due_at: object
    last_confidence: object
    version: integer
    created_at: string (required)
    updated_at: string (required)
    resolved_at: object
    preview: object
    message_count: integer
    last_message_at: object
    labels: array
    claimed_by: object
    claimed_at: object
    claim_expires_at: object
    claim_active: boolean
    sla_breached: boolean
    needs_response: boolean
    waiting_since: object
    first_response_at: object
    survey_url: object
    language: object
    ticket_id: object
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### POST `/api/conversations/{conversation_id}/assign`

**Assign to an operator**

Assign to an operator. Requires: operator:act.

*Tags:* `conversations`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `conversation_id` | path | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Request body**

`application/json`

{
  assignee_id: string (required)
}

**Responses**

- `200` Successful Response

  `application/json`

  {
    id: string (required)
    tenant_id: string (required)
    customer_name: string (required)
    customer_ref: object
    channel: string (required)
    status: string (required)
    intent: object
    assigned_agent: object
    priority: string (required)
    handoff_reason: object
    sla_due_at: object
    last_confidence: object
    version: integer
    created_at: string (required)
    updated_at: string (required)
    resolved_at: object
    preview: object
    message_count: integer
    last_message_at: object
    labels: array
    claimed_by: object
    claimed_at: object
    claim_expires_at: object
    claim_active: boolean
    sla_breached: boolean
    needs_response: boolean
    waiting_since: object
    first_response_at: object
    survey_url: object
    language: object
    ticket_id: object
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### POST `/api/conversations/{conversation_id}/claim`

**Claim a conversation**

Claim a conversation. Requires: operator:act.

*Tags:* `conversations`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `conversation_id` | path | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  {
    id: string (required)
    tenant_id: string (required)
    customer_name: string (required)
    customer_ref: object
    channel: string (required)
    status: string (required)
    intent: object
    assigned_agent: object
    priority: string (required)
    handoff_reason: object
    sla_due_at: object
    last_confidence: object
    version: integer
    created_at: string (required)
    updated_at: string (required)
    resolved_at: object
    preview: object
    message_count: integer
    last_message_at: object
    labels: array
    claimed_by: object
    claimed_at: object
    claim_expires_at: object
    claim_active: boolean
    sla_breached: boolean
    needs_response: boolean
    waiting_since: object
    first_response_at: object
    survey_url: object
    language: object
    ticket_id: object
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### POST `/api/conversations/{conversation_id}/feedback`

**Rate an assistant message**

Rate an assistant message. Requires: conversation:read.

*Tags:* `conversations`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `conversation_id` | path | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Request body**

`application/json`

{
  message_id: string (required)
  rating: integer (required)
  reason: object
}

**Responses**

- `200` Successful Response

  `application/json`

  {
    id: string (required)
    tenant_id: string (required)
    conversation_id: string (required)
    message_id: string (required)
    actor: string (required)
    rating: integer (required)
    reason: object
    created_at: string (required)
    updated_at: string (required)
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### PUT `/api/conversations/{conversation_id}/labels`

**Replace conversation labels**

Replace conversation labels. Requires: operator:act.

*Tags:* `conversations`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `conversation_id` | path | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Request body**

`application/json`

{
  labels: array
}

**Responses**

- `200` Successful Response

  `application/json`

  {
    id: string (required)
    tenant_id: string (required)
    customer_name: string (required)
    customer_ref: object
    channel: string (required)
    status: string (required)
    intent: object
    assigned_agent: object
    priority: string (required)
    handoff_reason: object
    sla_due_at: object
    last_confidence: object
    version: integer
    created_at: string (required)
    updated_at: string (required)
    resolved_at: object
    preview: object
    message_count: integer
    last_message_at: object
    labels: array
    claimed_by: object
    claimed_at: object
    claim_expires_at: object
    claim_active: boolean
    sla_breached: boolean
    needs_response: boolean
    waiting_since: object
    first_response_at: object
    survey_url: object
    language: object
    ticket_id: object
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### PATCH `/api/conversations/{conversation_id}/language`

**Set (or clear, with null) the manual language override for a conversation**

Set (or clear) the manual language override for a conversation.

Backlog (多语言客服): ``language`` null clears the override so the
writer path re-detects automatically. The endpoint is a full upsert —
it returns the stored row so the frontend can sync its select.

*Tags:* `conversations`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `conversation_id` | path | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Request body**

`application/json`

{
  language: object
}

**Responses**

- `200` Successful Response

  `application/json`

  {
    id: string (required)
    tenant_id: string (required)
    customer_name: string (required)
    customer_ref: object
    channel: string (required)
    status: string (required)
    intent: object
    assigned_agent: object
    priority: string (required)
    handoff_reason: object
    sla_due_at: object
    last_confidence: object
    version: integer
    created_at: string (required)
    updated_at: string (required)
    resolved_at: object
    preview: object
    message_count: integer
    last_message_at: object
    labels: array
    claimed_by: object
    claimed_at: object
    claim_expires_at: object
    claim_active: boolean
    sla_breached: boolean
    needs_response: boolean
    waiting_since: object
    first_response_at: object
    survey_url: object
    language: object
    ticket_id: object
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### GET `/api/conversations/{conversation_id}/messages`

**List conversation messages**

List conversation messages. Requires: conversation:read.

*Tags:* `conversations`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `conversation_id` | path | yes |  |
| `limit` | query | no |  |
| `cursor` | query | no |  |
| `before` | query | no |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  array of:
    {
      id: string (required)
      role: string (required)
      author: string (required)
      content: string (required)
      metadata: object
      created_at: string (required)
      reply_to: object
    }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### POST `/api/conversations/{conversation_id}/messages`

**Send a customer turn (idempotent)**

Send a customer turn (idempotent). Requires: conversation:write.

*Tags:* `conversations`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `conversation_id` | path | yes |  |
| `Idempotency-Key` | header | no |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Request body**

`application/json`

{
  content: string (required)
}

**Responses**

- `200` Successful Response

  `application/json`

  {
    customer_message: object (required)
      {
        id: string (required)
        role: string (required)
        author: string (required)
        content: string (required)
        metadata: object
        created_at: string (required)
        reply_to: object
      }
    assistant_message: object (required)
    conversation: object (required)
      {
        id: string (required)
        tenant_id: string (required)
        customer_name: string (required)
        customer_ref: object
        channel: string (required)
        status: string (required)
        intent: object
        assigned_agent: object
        priority: string (required)
        handoff_reason: object
        sla_due_at: object
        last_confidence: object
        version: integer
        created_at: string (required)
        updated_at: string (required)
        resolved_at: object
        preview: object
        message_count: integer
        last_message_at: object
        labels: array
        claimed_by: object
        claimed_at: object
        claim_expires_at: object
        claim_active: boolean
        sla_breached: boolean
        needs_response: boolean
        waiting_since: object
        first_response_at: object
        survey_url: object
        language: object
        ticket_id: object
      }
    idempotent_replay: boolean (required)
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### POST `/api/conversations/{conversation_id}/messages/{message_id}/translate`

**Translate one customer message; without a provider the original text is echoed**

Translate one customer message into ``target_language``.

Backlog (多语言客服): never blocks on model availability — without a
configured provider the original text is returned with
``was_translated=False`` and ``source="rule"`` so the frontend can
degrade gracefully.

*Tags:* `conversations`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `conversation_id` | path | yes |  |
| `message_id` | path | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Request body**

`application/json`

{
  target_language: string (required)
}

**Responses**

- `200` Successful Response

  `application/json`

  {
    translated: string (required)
    was_translated: boolean (required)
    source: string (required)
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### POST `/api/conversations/{conversation_id}/notes`

**Add an internal note (supports @mention colleagues and reply threads)**

Add an internal note (supports @mention colleagues and reply threads). Requires: operator:act.

*Tags:* `conversations`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `conversation_id` | path | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Request body**

`application/json`

{
  content: string (required)
  reply_to: object
}

**Responses**

- `200` Successful Response

  `application/json`

  {
    id: string (required)
    role: string (required)
    author: string (required)
    content: string (required)
    metadata: object
    created_at: string (required)
    reply_to: object
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### POST `/api/conversations/{conversation_id}/operator-messages`

**Send an operator reply**

Send an operator reply. Requires: operator:act.

*Tags:* `conversations`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `conversation_id` | path | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Request body**

`application/json`

{
  content: string (required)
  attachment_ids: array
}

**Responses**

- `200` Successful Response

  `application/json`

  {
    id: string (required)
    role: string (required)
    author: string (required)
    content: string (required)
    metadata: object
    created_at: string (required)
    reply_to: object
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### POST `/api/conversations/{conversation_id}/release`

**Release a claim**

Release a claim. Requires: operator:act.

*Tags:* `conversations`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `conversation_id` | path | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  {
    id: string (required)
    tenant_id: string (required)
    customer_name: string (required)
    customer_ref: object
    channel: string (required)
    status: string (required)
    intent: object
    assigned_agent: object
    priority: string (required)
    handoff_reason: object
    sla_due_at: object
    last_confidence: object
    version: integer
    created_at: string (required)
    updated_at: string (required)
    resolved_at: object
    preview: object
    message_count: integer
    last_message_at: object
    labels: array
    claimed_by: object
    claimed_at: object
    claim_expires_at: object
    claim_active: boolean
    sla_breached: boolean
    needs_response: boolean
    waiting_since: object
    first_response_at: object
    survey_url: object
    language: object
    ticket_id: object
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### POST `/api/conversations/{conversation_id}/reopen`

**Reopen a resolved conversation**

Reopen a resolved conversation. Requires: operator:act.

*Tags:* `conversations`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `conversation_id` | path | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  {
    id: string (required)
    tenant_id: string (required)
    customer_name: string (required)
    customer_ref: object
    channel: string (required)
    status: string (required)
    intent: object
    assigned_agent: object
    priority: string (required)
    handoff_reason: object
    sla_due_at: object
    last_confidence: object
    version: integer
    created_at: string (required)
    updated_at: string (required)
    resolved_at: object
    preview: object
    message_count: integer
    last_message_at: object
    labels: array
    claimed_by: object
    claimed_at: object
    claim_expires_at: object
    claim_active: boolean
    sla_breached: boolean
    needs_response: boolean
    waiting_since: object
    first_response_at: object
    survey_url: object
    language: object
    ticket_id: object
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### POST `/api/conversations/{conversation_id}/resolve`

**Resolve a conversation**

Resolve a conversation. Requires: operator:act.

*Tags:* `conversations`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `conversation_id` | path | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  {
    id: string (required)
    tenant_id: string (required)
    customer_name: string (required)
    customer_ref: object
    channel: string (required)
    status: string (required)
    intent: object
    assigned_agent: object
    priority: string (required)
    handoff_reason: object
    sla_due_at: object
    last_confidence: object
    version: integer
    created_at: string (required)
    updated_at: string (required)
    resolved_at: object
    preview: object
    message_count: integer
    last_message_at: object
    labels: array
    claimed_by: object
    claimed_at: object
    claim_expires_at: object
    claim_active: boolean
    sla_breached: boolean
    needs_response: boolean
    waiting_since: object
    first_response_at: object
    survey_url: object
    language: object
    ticket_id: object
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

## Copilot

### POST `/api/copilot/knowledge`

**Recommend knowledge articles for the latest customer message**

Recommend knowledge articles for the latest customer message. Requires: operator:act.

*Tags:* `copilot`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Request body**

`application/json`

{
  conversation_id: string (required)
  query: object
  limit: integer
}

**Responses**

- `200` Successful Response

  `application/json`

  {
    articles: array
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### POST `/api/copilot/rewrite`

**Rewrite an operator draft in a requested tone (best-effort)**

Rewrite an operator draft in a requested tone (best-effort). Requires: operator:act.

*Tags:* `copilot`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Request body**

`application/json`

{
  text: string (required)
  tone: string
}

**Responses**

- `200` Successful Response

  `application/json`

  {
    rewritten: string (required)
    source: string (required)
    tone: string (required)
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### POST `/api/copilot/suggest`

**Suggest 1-3 customer-facing reply drafts for a conversation**

Suggest 1-3 customer-facing reply drafts for a conversation. Requires: operator:act.

*Tags:* `copilot`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Request body**

`application/json`

{
  conversation_id: string (required)
  draft: object
  limit: integer
}

**Responses**

- `200` Successful Response

  `application/json`

  {
    suggestions: array
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

## Csat

### POST `/api/csat/{token}`

**Submit a one-time CSAT satisfaction rating**

Record a one-time CSAT rating for a resolved conversation.

Accepts either a JSON body (``{"rating": 1-5}``, the API contract) or a
browser ``application/x-www-form-urlencoded`` submission from the survey
form. Both are routed through the same atomic single-use write, and a
browser submission receives a thank-you page back.

*Tags:* `csat`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `token` | path | yes |  |

**Responses**

- `200` Successful Response

  `application/json`



- `422` Validation Error

  `application/json`

  {
    detail: array
  }

## Dashboard

### GET `/api/dashboard`

**Queue and quality dashboard indicators**

Queue and quality dashboard indicators. Requires: conversation:read.

*Tags:* `dashboard`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  {
    open: integer (required)
    waiting_human: integer (required)
    human_active: integer (required)
    resolved: integer (required)
    total: integer (required)
    sla_breached: integer (required)
    claimed_active: integer
    high_priority: integer
    needs_response: integer
    average_first_response_seconds: number
    automated_responses: integer (required)
    average_confidence: number (required)
    grounded_rate: number (required)
    positive_feedback_rate: number (required)
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

## Knowledge

### POST `/api/conversations/{conversation_id}/messages/{message_id}/knowledge-draft`

**Create a draft from a negatively-rated message**

Generate a draft knowledge article from a negatively-rated message.

Reflows negative feedback into the knowledge base (Phase 21.3): the
assistant message's content seeds a draft the reviewer can edit and
publish.  Returns 404 if the message or conversation does not exist.

*Tags:* `knowledge`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `conversation_id` | path | yes |  |
| `message_id` | path | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `201` Successful Response

  `application/json`

  {
    id: string (required)
    tenant_id: string (required)
    title: string (required)
    content: string (required)
    tags: array (required)
    category: string (required)
    source_url: string (required)
    active: boolean (required)
    status: string
    version: integer (required)
    updated_at: string (required)
    reviewed_by: object
    reviewed_at: object
    language: object
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### GET `/api/knowledge`

**List published knowledge articles**

List published knowledge articles. Requires: conversation:read.

*Tags:* `knowledge`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `include_inactive` | query | no |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  array of:
    {
      id: string (required)
      tenant_id: string (required)
      title: string (required)
      content: string (required)
      tags: array (required)
      category: string (required)
      source_url: string (required)
      active: boolean (required)
      status: string
      version: integer (required)
      updated_at: string (required)
      reviewed_by: object
      reviewed_at: object
      language: object
    }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### POST `/api/knowledge`

**Create a published knowledge article**

Create a published knowledge article. Requires: knowledge:write.

*Tags:* `knowledge`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Request body**

`application/json`

{
  title: string (required)
  content: string (required)
  tags: array (required)
  category: string
  source_url: string (required)
  language: object
}

**Responses**

- `201` Successful Response

  `application/json`

  {
    id: string (required)
    tenant_id: string (required)
    title: string (required)
    content: string (required)
    tags: array (required)
    category: string (required)
    source_url: string (required)
    active: boolean (required)
    status: string
    version: integer (required)
    updated_at: string (required)
    reviewed_by: object
    reviewed_at: object
    language: object
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### POST `/api/knowledge/drafts`

**Create a knowledge draft (invisible until approved)**

Create a knowledge article in ``draft`` status (Phase 21.3).

Drafts are invisible to retrieval until explicitly published through
the review endpoint, so an approval step is mandatory.

*Tags:* `knowledge`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Request body**

`application/json`

{
  title: string (required)
  content: string (required)
  tags: array (required)
  category: string
  source_url: string (required)
  language: object
}

**Responses**

- `201` Successful Response

  `application/json`

  {
    id: string (required)
    tenant_id: string (required)
    title: string (required)
    content: string (required)
    tags: array (required)
    category: string (required)
    source_url: string (required)
    active: boolean (required)
    status: string
    version: integer (required)
    updated_at: string (required)
    reviewed_by: object
    reviewed_at: object
    language: object
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### PATCH `/api/knowledge/{article_id}`

**Update a knowledge article**

Update a knowledge article. Requires: knowledge:write.

*Tags:* `knowledge`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `article_id` | path | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Request body**

`application/json`

{
  title: object
  content: object
  tags: object
  category: object
  source_url: object
  active: object
  language: object
}

**Responses**

- `200` Successful Response

  `application/json`

  {
    id: string (required)
    tenant_id: string (required)
    title: string (required)
    content: string (required)
    tags: array (required)
    category: string (required)
    source_url: string (required)
    active: boolean (required)
    status: string
    version: integer (required)
    updated_at: string (required)
    reviewed_by: object
    reviewed_at: object
    language: object
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### POST `/api/knowledge/{article_id}/review`

**Publish or retire a pending knowledge article**

Approve (publish) or reject (retire) a pending knowledge article.

The approval cannot be bypassed: only ``draft`` or ``pending_review``
articles can be published, so a reviewer must act before the article
becomes retrievable.

*Tags:* `knowledge`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `article_id` | path | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Request body**

`application/json`

{
  action: string (required)
  notes: object
}

**Responses**

- `200` Successful Response

  `application/json`

  {
    id: string (required)
    tenant_id: string (required)
    title: string (required)
    content: string (required)
    tags: array (required)
    category: string (required)
    source_url: string (required)
    active: boolean (required)
    status: string
    version: integer (required)
    updated_at: string (required)
    reviewed_by: object
    reviewed_at: object
    language: object
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

## Privacy

### POST `/api/data-subject-requests`

**Create a data subject request**

Create a data subject request. Requires: admin:manage.

*Tags:* `privacy`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `customer_ref` | query | yes |  |
| `request_type` | query | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `201` Successful Response

  `application/json`

  object

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### POST `/api/data-subject-requests/{customer_ref}/execute`

**Execute a data subject request**

Execute a data subject request. Requires: admin:manage.

*Tags:* `privacy`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `customer_ref` | path | yes |  |
| `request_type` | query | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  object

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

## Prompts

### GET `/api/prompts`

**List prompt versions**

List prompt versions. Requires: admin:manage.

*Tags:* `prompts`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `name` | query | no |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  array of:
    {
      id: string (required)
      tenant_id: object (required)
      name: string (required)
      version: string (required)
      body: string (required)
      model_ref: string (required)
      status: string (required)
      created_by: string (required)
      created_at: string (required)
      updated_at: string (required)
      activated_at: object (required)
    }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### POST `/api/prompts`

**Register a prompt version**

Register a prompt version. Requires: admin:manage.

*Tags:* `prompts`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Request body**

`application/json`

{
  name: string (required)
  version: string (required)
  body: string (required)
  model_ref: string (required)
}

**Responses**

- `201` Successful Response

  `application/json`

  {
    id: string (required)
    tenant_id: object (required)
    name: string (required)
    version: string (required)
    body: string (required)
    model_ref: string (required)
    status: string (required)
    created_by: string (required)
    created_at: string (required)
    updated_at: string (required)
    activated_at: object (required)
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### POST `/api/prompts/{version_id}/action`

**Activate, canary, or rollback a prompt version**

Activate, canary, or rollback a prompt version. Requires: admin:manage.

*Tags:* `prompts`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `version_id` | path | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Request body**

`application/json`

{
  action: string (required)
}

**Responses**

- `200` Successful Response

  `application/json`

  {
    id: string (required)
    tenant_id: object (required)
    name: string (required)
    version: string (required)
    body: string (required)
    model_ref: string (required)
    status: string (required)
    created_by: string (required)
    created_at: string (required)
    updated_at: string (required)
    activated_at: object (required)
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

## Quality

### GET `/api/supervisor/knowledge-gaps`

**Negative-feedback turns without knowledge citations**

Surface negative-feedback turns with no knowledge citations.

Used by the supervisor quality panel (Phase 21.2) to locate where the
knowledge base is failing customers and seed draft articles.

*Tags:* `quality`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `limit` | query | no |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  array of object

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### GET `/api/supervisor/quality`

**Quality buckets by day/intent/prompt_version**

Quality buckets by day/intent/prompt_version. Requires: metrics:read.

*Tags:* `quality`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `since` | query | no |  |
| `until` | query | no |  |
| `intent` | query | no |  |
| `prompt_version` | query | no |  |
| `cursor` | query | no |  |
| `limit` | query | no |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  array of:
    {
      date: string (required)
      intent: string (required)
      prompt_version: string (required)
      turn_count: integer (required)
      escalation_count: integer (required)
      negative_feedback_count: integer (required)
      escalation_rate: number (required)
      negative_feedback_rate: number (required)
      avg_first_response_seconds: number (required)
      avg_latency_ms: number (required)
      estimated_tokens: integer (required)
    }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

## Queues

### GET `/api/events/queue`

**SSE queue updates**

SSE queue updates. Requires: conversation:read.

*Tags:* `queues`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `timeout` | query | no |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`



- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### GET `/api/saved-views`

**List saved queue views**

List saved queue views. Requires: conversation:read.

*Tags:* `queues`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  array of:
    {
      id: string (required)
      tenant_id: string (required)
      actor_id: string (required)
      name: string (required)
      filters: object (required)
        {
          status: object
          search: object
          label: object
          priority: object
          channel: object
          sort: object
          ownership: object
        }
      created_at: string (required)
      updated_at: string (required)
    }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### POST `/api/saved-views`

**Create a saved queue view**

Create a saved queue view. Requires: conversation:read.

*Tags:* `queues`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Request body**

`application/json`

{
  name: string (required)
  filters: object (required)
    {
      status: object
      search: object
      label: object
      priority: object
      channel: object
      sort: object
      ownership: object
    }
}

**Responses**

- `201` Successful Response

  `application/json`

  {
    id: string (required)
    tenant_id: string (required)
    actor_id: string (required)
    name: string (required)
    filters: object (required)
      {
        status: object
        search: object
        label: object
        priority: object
        channel: object
        sort: object
        ownership: object
      }
    created_at: string (required)
    updated_at: string (required)
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### DELETE `/api/saved-views/{view_id}`

**Delete a saved queue view**

Delete a saved queue view. Requires: conversation:read.

*Tags:* `queues`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `view_id` | path | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `204` Successful Response

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

## Reports

### POST `/api/admin/report-subscriptions`

**Create a scheduled report subscription (webhook delivery)**

Create a scheduled report subscription (webhook delivery). Requires: admin:manage.

*Tags:* `reports`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Request body**

`application/json`

{
  report_type: string (required)
  schedule: string
  window_days: integer
  webhook_endpoint_id: string (required)
}

**Responses**

- `201` Successful Response

  `application/json`

  {
    id: string (required)
    tenant_id: string (required)
    report_type: string (required)
    schedule: string (required)
    window_days: integer (required)
    webhook_endpoint_id: string (required)
    active: boolean (required)
    created_by: string (required)
    created_at: string (required)
    updated_at: string (required)
    last_run_at: object
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### GET `/api/admin/report-subscriptions`

**List report subscriptions**

List report subscriptions. Requires: admin:manage.

*Tags:* `reports`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  array of:
    {
      id: string (required)
      tenant_id: string (required)
      report_type: string (required)
      schedule: string (required)
      window_days: integer (required)
      webhook_endpoint_id: string (required)
      active: boolean (required)
      created_by: string (required)
      created_at: string (required)
      updated_at: string (required)
      last_run_at: object
    }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### PATCH `/api/admin/report-subscriptions/{subscription_id}`

**Update a report subscription (active/schedule/window)**

Update a report subscription (active/schedule/window). Requires: admin:manage.

*Tags:* `reports`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `subscription_id` | path | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Request body**

`application/json`

{
  active: object
  schedule: object
  window_days: object
}

**Responses**

- `200` Successful Response

  `application/json`

  {
    id: string (required)
    tenant_id: string (required)
    report_type: string (required)
    schedule: string (required)
    window_days: integer (required)
    webhook_endpoint_id: string (required)
    active: boolean (required)
    created_by: string (required)
    created_at: string (required)
    updated_at: string (required)
    last_run_at: object
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### DELETE `/api/admin/report-subscriptions/{subscription_id}`

**Delete a report subscription**

Delete a report subscription. Requires: admin:manage.

*Tags:* `reports`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `subscription_id` | path | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  object

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### POST `/api/admin/reports/generate`

**Generate a quality/usage report on demand (optionally deliver)**

Generate a quality/usage report on demand (optionally deliver). Requires: admin:manage.

*Tags:* `reports`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Request body**

`application/json`

{
  report_type: string (required)
  window_days: integer
  webhook_endpoint_id: object
}

**Responses**

- `200` Successful Response

  `application/json`

  {
    report_type: string (required)
    from_date: string (required)
    to_date: string (required)
    window_days: integer (required)
    rows: array
    generated_at: string (required)
    deliveries: integer
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### GET `/api/admin/reports/{report_type}/export`

**Export a quality/usage report as CSV**

Export a quality/usage report as CSV. Requires: admin:manage.

*Tags:* `reports`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `report_type` | path | yes |  |
| `from` | query | no |  |
| `to` | query | no |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`



- `422` Validation Error

  `application/json`

  {
    detail: array
  }

## System

### GET `/api/system/metrics`

**Runtime metrics snapshot**

Runtime metrics snapshot. Requires: metrics:read.

*Tags:* `system`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  object

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### GET `/health`

**Liveness probe**

Liveness probe. Requires: none (unauthenticated).

*Tags:* `system`

**Responses**

- `200` Successful Response

  `application/json`

  object

### GET `/health/live`

**Liveness probe**

Liveness probe. Requires: none (unauthenticated).

*Tags:* `system`

**Responses**

- `200` Successful Response

  `application/json`

  object

### GET `/health/ready`

**Readiness probe (database reachable)**

Readiness probe (database reachable). Requires: none (unauthenticated).

*Tags:* `system`

**Responses**

- `200` Successful Response

  `application/json`



### GET `/health/startup`

**Startup probe (app serving, before dependencies ready)**

Startup probe (Phase 30.2): true once the app is serving.

Distinct from liveness (process alive) and readiness (dependencies
reachable): a freshly-started instance may report startup=ok before
readiness=ok while it initializes.

*Tags:* `system`

**Responses**

- `200` Successful Response

  `application/json`



## Tickets

### POST `/api/tickets`

**Convert a conversation into a long-cycle ticket (idempotent)**

Convert a conversation into a long-cycle ticket (idempotent). Requires: operator:act.

*Tags:* `tickets`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Request body**

`application/json`

{
  conversation_id: string (required)
  subject: string (required)
  description: object
  priority: string
}

**Responses**

- `201` Successful Response

  `application/json`

  {
    id: string (required)
    tenant_id: string (required)
    subject: string (required)
    description: object
    status: string (required)
    priority: string (required)
    assigned_agent: object
    customer_name: string (required)
    customer_ref: object
    source_conversation_id: object
    created_at: string (required)
    updated_at: string (required)
    closed_at: object
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### GET `/api/tickets`

**List tickets (filter by status or customer reference)**

List tickets (filter by status or customer reference). Requires: conversation:read.

*Tags:* `tickets`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `status` | query | no |  |
| `customer_ref` | query | no |  |
| `limit` | query | no |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  array of:
    {
      id: string (required)
      tenant_id: string (required)
      subject: string (required)
      description: object
      status: string (required)
      priority: string (required)
      assigned_agent: object
      customer_name: string (required)
      customer_ref: object
      source_conversation_id: object
      created_at: string (required)
      updated_at: string (required)
      closed_at: object
    }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### GET `/api/tickets/{ticket_id}`

**Ticket detail with linked conversations**

Ticket detail with linked conversations. Requires: conversation:read.

*Tags:* `tickets`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `ticket_id` | path | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  {
    id: string (required)
    tenant_id: string (required)
    subject: string (required)
    description: object
    status: string (required)
    priority: string (required)
    assigned_agent: object
    customer_name: string (required)
    customer_ref: object
    source_conversation_id: object
    created_at: string (required)
    updated_at: string (required)
    closed_at: object
    conversations: array
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### PATCH `/api/tickets/{ticket_id}`

**Update ticket subject/description/priority/assignee**

Update ticket subject/description/priority/assignee. Requires: operator:act.

*Tags:* `tickets`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `ticket_id` | path | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Request body**

`application/json`

{
  subject: object
  description: object
  priority: object
  assigned_agent: object
}

**Responses**

- `200` Successful Response

  `application/json`

  {
    id: string (required)
    tenant_id: string (required)
    subject: string (required)
    description: object
    status: string (required)
    priority: string (required)
    assigned_agent: object
    customer_name: string (required)
    customer_ref: object
    source_conversation_id: object
    created_at: string (required)
    updated_at: string (required)
    closed_at: object
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### POST `/api/tickets/{ticket_id}/link`

**Link another conversation to the ticket (cross-conversation tracking)**

Link another conversation to the ticket (cross-conversation tracking). Requires: operator:act.

*Tags:* `tickets`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `ticket_id` | path | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Request body**

`application/json`

{
  conversation_id: string (required)
}

**Responses**

- `200` Successful Response

  `application/json`

  {
    id: string (required)
    tenant_id: string (required)
    subject: string (required)
    description: object
    status: string (required)
    priority: string (required)
    assigned_agent: object
    customer_name: string (required)
    customer_ref: object
    source_conversation_id: object
    created_at: string (required)
    updated_at: string (required)
    closed_at: object
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### POST `/api/tickets/{ticket_id}/transition`

**Move a ticket through its state machine (open/in_progress/closed)**

Move a ticket through its state machine (open/in_progress/closed). Requires: operator:act.

*Tags:* `tickets`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `ticket_id` | path | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Request body**

`application/json`

{
  status: string (required)
  reason: object
}

**Responses**

- `200` Successful Response

  `application/json`

  {
    id: string (required)
    tenant_id: string (required)
    subject: string (required)
    description: object
    status: string (required)
    priority: string (required)
    assigned_agent: object
    customer_name: string (required)
    customer_ref: object
    source_conversation_id: object
    created_at: string (required)
    updated_at: string (required)
    closed_at: object
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

## Turn-jobs

### POST `/api/conversations/{conversation_id}/turn-jobs`

**Enqueue an async turn**

Enqueue an async turn. Requires: conversation:write.

*Tags:* `turn-jobs`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `conversation_id` | path | yes |  |
| `Idempotency-Key` | header | no |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Request body**

`application/json`

{
  content: string (required)
}

**Responses**

- `202` Successful Response

  `application/json`

  {
    id: string (required)
    tenant_id: string (required)
    conversation_id: string (required)
    status: string (required)
    attempts: integer (required)
    max_attempts: integer (required)
    available_at: string (required)
    locked_at: object
    error_code: object
    created_at: string (required)
    updated_at: string (required)
    completed_at: object
    idempotent_replay: boolean
    result: object
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### GET `/api/turn-jobs`

**List turn jobs**

List turn jobs. Requires: conversation:read.

*Tags:* `turn-jobs`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `status` | query | no |  |
| `limit` | query | no |  |
| `offset` | query | no |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  array of:
    {
      id: string (required)
      tenant_id: string (required)
      conversation_id: string (required)
      status: string (required)
      attempts: integer (required)
      max_attempts: integer (required)
      available_at: string (required)
      locked_at: object
      error_code: object
      created_at: string (required)
      updated_at: string (required)
      completed_at: object
      idempotent_replay: boolean
      result: object
    }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### GET `/api/turn-jobs/{job_id}`

**Fetch a turn job with result**

Fetch a turn job with result. Requires: conversation:read.

*Tags:* `turn-jobs`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `job_id` | path | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  {
    id: string (required)
    tenant_id: string (required)
    conversation_id: string (required)
    status: string (required)
    attempts: integer (required)
    max_attempts: integer (required)
    available_at: string (required)
    locked_at: object
    error_code: object
    created_at: string (required)
    updated_at: string (required)
    completed_at: object
    idempotent_replay: boolean
    result: object
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### GET `/api/turn-jobs/{job_id}/events`

**SSE stream for a turn job**

SSE stream for a turn job. Requires: conversation:read.

*Tags:* `turn-jobs`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `job_id` | path | yes |  |
| `timeout` | query | no |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`



- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### POST `/api/turn-jobs/{job_id}/retry`

**Retry a failed turn job**

Retry a failed turn job. Requires: conversation:write.

*Tags:* `turn-jobs`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `job_id` | path | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `202` Successful Response

  `application/json`

  {
    id: string (required)
    tenant_id: string (required)
    conversation_id: string (required)
    status: string (required)
    attempts: integer (required)
    max_attempts: integer (required)
    available_at: string (required)
    locked_at: object
    error_code: object
    created_at: string (required)
    updated_at: string (required)
    completed_at: object
    idempotent_replay: boolean
    result: object
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

## Ui

### GET `/`

**Operator workspace**

Operator workspace. Requires: none (unauthenticated).

*Tags:* `ui`

**Responses**

- `200` Successful Response

  `text/html`

  string

## Webhooks

### POST `/api/webhooks`

**Register a webhook endpoint**

Register a webhook endpoint. Requires: admin:manage.

*Tags:* `webhooks`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Request body**

`application/json`

{
  url: string (required)
  events: array (required)
  secret: string (required)
}

**Responses**

- `201` Successful Response

  `application/json`

  {
    id: string (required)
    tenant_id: string (required)
    url: string (required)
    events: array (required)
    status: string (required)
    created_at: string (required)
    updated_at: string (required)
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### GET `/api/webhooks`

**List webhook endpoints**

List webhook endpoints. Requires: admin:manage.

*Tags:* `webhooks`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  array of:
    {
      id: string (required)
      tenant_id: string (required)
      url: string (required)
      events: array (required)
      status: string (required)
      created_at: string (required)
      updated_at: string (required)
    }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### GET `/api/webhooks/deliveries`

**List webhook deliveries**

List webhook deliveries. Requires: admin:manage.

*Tags:* `webhooks`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `endpoint_id` | query | no |  |
| `limit` | query | no |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  array of:
    {
      id: string (required)
      tenant_id: string (required)
      endpoint_id: string (required)
      event_type: string (required)
      event_id: string (required)
      status: string (required)
      attempts: integer (required)
      max_attempts: integer (required)
      next_attempt_at: string (required)
      last_response_code: object
      last_error: object
      created_at: string (required)
      updated_at: string (required)
    }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### DELETE `/api/webhooks/{endpoint_id}`

**Delete a webhook endpoint**

Delete a webhook endpoint. Requires: admin:manage.

*Tags:* `webhooks`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `endpoint_id` | path | yes |  |
| `X-API-Key` | header | no |  |
| `X-Tenant-Id` | header | no |  |

**Responses**

- `204` Successful Response

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

## Widget

### POST `/api/widget/sessions`

**Open a widget chat session (signed token)**

Open a widget chat session bound to the token's tenant.

*Tags:* `widget`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `X-Widget-Token` | header | no |  |

**Request body**

`application/json`

{
  customer_name: object
  channel: string
}

**Responses**

- `201` Successful Response

  `application/json`

  {
    conversation: object (required)
      {
        id: string (required)
        tenant_id: string (required)
        customer_name: string (required)
        customer_ref: object
        channel: string (required)
        status: string (required)
        intent: object
        assigned_agent: object
        priority: string (required)
        handoff_reason: object
        sla_due_at: object
        last_confidence: object
        version: integer
        created_at: string (required)
        updated_at: string (required)
        resolved_at: object
        preview: object
        message_count: integer
        last_message_at: object
        labels: array
        claimed_by: object
        claimed_at: object
        claim_expires_at: object
        claim_active: boolean
        sla_breached: boolean
        needs_response: boolean
        waiting_since: object
        first_response_at: object
        survey_url: object
        language: object
        ticket_id: object
      }
    widget_token: string (required)
  }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### POST `/api/widget/sessions/{conversation_id}/messages`

**Send a widget message (channel-id idempotent)**

Send a customer message; channel_message_id replays are idempotent.

Default (sync) returns the completed ``TurnResponse``. With
``async_mode=true`` the message is enqueued as a turn job and the
response is ``{"job_id": ..., "status": "queued"}``; the client then
streams progressive output from ``GET /stream``. Either way, replaying
the same ``channel_message_id`` never creates a second turn.

*Tags:* `widget`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `conversation_id` | path | yes |  |
| `async_mode` | query | no |  |
| `X-Widget-Token` | header | no |  |

**Request body**

`application/json`

{
  content: string (required)
  channel_message_id: object
}

**Responses**

- `200` Successful Response

  `application/json`

  object

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### GET `/api/widget/sessions/{conversation_id}/messages`

**List widget conversation messages**

List widget conversation messages. Requires: X-Widget-Token (signed, no API key).

*Tags:* `widget`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `conversation_id` | path | yes |  |
| `limit` | query | no |  |
| `X-Widget-Token` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`

  array of:
    {
      id: string (required)
      role: string (required)
      author: string (required)
      content: string (required)
      metadata: object
      created_at: string (required)
      reply_to: object
    }

- `422` Validation Error

  `application/json`

  {
    detail: array
  }

### GET `/api/widget/sessions/{conversation_id}/stream`

**SSE stream for the widget conversation's latest turn**

SSE stream of the conversation's latest turn job (Phase 23.1).

Reuses the same token/job events as the operator turn-job stream so the
widget gets progressive output without holding an API key.

*Tags:* `widget`

**Parameters**

| Name | In | Required | Description |
|------|----|----------|-------------|
| `conversation_id` | path | yes |  |
| `timeout` | query | no |  |
| `X-Widget-Token` | header | no |  |

**Responses**

- `200` Successful Response

  `application/json`



- `422` Validation Error

  `application/json`

  {
    detail: array
  }
