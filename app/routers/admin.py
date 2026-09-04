"""Admin routes: prompts, tenants, quota, members, usage, retention, webhooks (27.2)."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.audit_gap import audit_high_risk, list_gaps, merge_gap_rows
from app.credentials import (
    API_KEY_CREDENTIAL_ID_PREFIX_LENGTH,
    CredentialStatus,
    InvalidCredentialTransition,
    issue_api_key,
    key_ref_for,
)
from app.db._util import utc_now
from app.dsr import DsrExportError
from app.envelope_crypto import EnvelopeCryptoError
from app.main import (
    require_permission,
)
from app.routers.common import RouteDeps
from app.schemas import (
    AgentGroupCreateRequest,
    AgentGroupMemberOut,
    AgentGroupOut,
    CsatSummaryOut,
    DsrCreateRequest,
    MemberInviteRequest,
    MemberRoleUpdateRequest,
    PromptVersionActionRequest,
    PromptVersionCreateRequest,
    PromptVersionOut,
    RoutingRuleCreateRequest,
    RoutingRuleOut,
    SlaPolicyOut,
    SlaPolicyRequest,
    TenantMemberOut,
    TenantModelPolicyOut,
    TenantModelPolicyRequest,
    TenantProvisionRequest,
    TenantQuotaOut,
    TenantQuotaUpdateRequest,
    TenantUsageRowOut,
    WebhookCreateRequest,
    WebhookDeliveryOut,
    WebhookOut,
)
from app.security import Principal

logger = logging.getLogger("helix")


def _ensure_same_tenant(principal: Principal, tenant_id: str) -> None:
    # A tenant admin can only manage its own model policy; cross-tenant
    # access is refused even with admin:manage until a system-administrator
    # role is introduced.
    if tenant_id != principal.tenant_id:
        raise HTTPException(status_code=403, detail="Cannot manage another tenant's policy")


def _guard_keeps_an_admin(members: list[dict[str, Any]], target_actor_id: str) -> None:
    """W1: a demotion/deactivation must never leave a tenant with zero active
    admin members.

    The self-guard on the routes only catches the operator demoting or
    deactivating themselves.  A credential-scoped admin (whose API key declares
    ``role=admin`` but who is *not* a ``tenant_members`` row, e.g. an
    integration principal) can otherwise remove the tenant's last rostered
    admin, locking the tenant out of member management entirely.  Callers pass
    the full roster and the target id *before* applying the mutation; when the
    target is an active admin, the mutation must leave at least one other
    active admin behind.
    """
    target = next((m for m in members if m["actor_id"] == target_actor_id), None)
    if not (target and target["role"] == "admin" and target["status"] == "active"):
        return
    other_admins = [
        m
        for m in members
        if m["role"] == "admin" and m["status"] == "active" and m["actor_id"] != target_actor_id
    ]
    if not other_admins:
        raise HTTPException(
            status_code=409,
            detail="Cannot remove the last active admin member: at least one admin is required",
        )


def build_router(deps: RouteDeps) -> APIRouter:
    router = APIRouter()
    database = deps.database
    services = deps.services

    @router.get("/api/prompts", response_model=list[PromptVersionOut])
    def list_prompt_versions(
        principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
        name: str | None = None,
    ) -> list[PromptVersionOut]:
        from app.prompts import PromptRegistry

        registry = PromptRegistry(database)
        versions = registry.list_versions(principal.tenant_id, name)
        return [
            PromptVersionOut(
                id=v.id,
                tenant_id=v.tenant_id,
                name=v.name,
                version=v.version,
                body=v.body,
                model_ref=v.model_ref,
                status=v.status,
                created_by=v.created_by,
                created_at=v.created_at,
                updated_at=v.updated_at,
                activated_at=v.activated_at,
            )
            for v in versions
        ]

    @router.post("/api/prompts", response_model=PromptVersionOut, status_code=201)
    def create_prompt_version(
        principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
        request: PromptVersionCreateRequest,
    ) -> PromptVersionOut:
        from app.prompts import PromptRegistry

        registry = PromptRegistry(database)
        version = registry.create_version(
            tenant_id=principal.tenant_id,
            name=request.name,
            version=request.version,
            body=request.body,
            model_ref=request.model_ref,
            actor_id=principal.actor_id,
        )
        return PromptVersionOut(
            id=version.id,
            tenant_id=version.tenant_id,
            name=version.name,
            version=version.version,
            body=version.body,
            model_ref=version.model_ref,
            status=version.status,
            created_by=version.created_by,
            created_at=version.created_at,
            updated_at=version.updated_at,
            activated_at=version.activated_at,
        )

    @router.post("/api/prompts/{version_id}/action", response_model=PromptVersionOut)
    def prompt_version_action(
        principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
        version_id: str,
        request: PromptVersionActionRequest,
    ) -> PromptVersionOut:
        from app.prompts import PromptRegistry

        registry = PromptRegistry(database)

        if request.action == "activate":
            version = registry.activate(principal.tenant_id, version_id, principal.actor_id)
        elif request.action == "canary":
            try:
                version = registry.set_canary(principal.tenant_id, version_id, principal.actor_id)
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        elif request.action == "rollback":
            # Get the version to find its name for rollback
            target = registry.get_version(principal.tenant_id, version_id)
            if not target:
                raise HTTPException(status_code=404, detail="Prompt version not found")
            version = registry.rollback(principal.tenant_id, target.name, principal.actor_id)
            if not version:
                raise HTTPException(
                    status_code=404, detail="No retired version found to rollback to"
                )
        else:
            raise HTTPException(status_code=400, detail="Invalid action")

        return PromptVersionOut(
            id=version.id,
            tenant_id=version.tenant_id,
            name=version.name,
            version=version.version,
            body=version.body,
            model_ref=version.model_ref,
            status=version.status,
            created_by=version.created_by,
            created_at=version.created_at,
            updated_at=version.updated_at,
            activated_at=version.activated_at,
        )

    # ------------------------------------------------------------------
    # Tenant model policy and budget (Phase 19.4)
    # ------------------------------------------------------------------

    @router.get(
        "/api/admin/tenants/{tenant_id}/model-policy",
        response_model=TenantModelPolicyOut,
    )
    def get_tenant_model_policy_route(
        principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
        tenant_id: str,
    ) -> TenantModelPolicyOut:
        _ensure_same_tenant(principal, tenant_id)
        policy = database.get_tenant_model_policy(tenant_id)
        count = database.get_tenant_daily_usage(tenant_id, utc_now()[:10])
        return TenantModelPolicyOut(
            tenant_id=tenant_id,
            allowed_models=policy["allowed_models"],
            daily_turn_budget=policy["daily_turn_budget"],
            daily_turn_count=count,
        )

    @router.put(
        "/api/admin/tenants/{tenant_id}/model-policy",
        response_model=TenantModelPolicyOut,
    )
    def set_tenant_model_policy_route(
        principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
        tenant_id: str,
        request: TenantModelPolicyRequest,
    ) -> TenantModelPolicyOut:
        _ensure_same_tenant(principal, tenant_id)
        database.set_tenant_model_policy(
            tenant_id, request.allowed_models, request.daily_turn_budget
        )
        count = database.get_tenant_daily_usage(tenant_id, utc_now()[:10])
        return TenantModelPolicyOut(
            tenant_id=tenant_id,
            allowed_models=request.allowed_models,
            daily_turn_budget=request.daily_turn_budget,
            daily_turn_count=count,
        )

    # ------------------------------------------------------------------
    # Phase 22: tenant self-service, members, and metering.
    # ------------------------------------------------------------------

    @router.post(
        "/api/admin/tenants",
        response_model=TenantQuotaOut,
        status_code=201,
    )
    def provision_tenant_route(
        payload: TenantProvisionRequest,
        principal: Annotated[Principal, Depends(require_permission("tenant:manage"))],
    ) -> TenantQuotaOut:
        """Idempotently provision a tenant with default policy (Phase 22.1).

        Creating the same tenant id twice returns the tenant (idempotent);
        quota fields given here are applied on both calls.
        """
        quota = database.provision_tenant(
            payload.tenant_id,
            payload.name,
            principal.actor_id,
            allowed_models=payload.allowed_models,
            daily_turn_budget=payload.daily_turn_budget,
            conversation_quota=payload.conversation_quota,
            storage_quota_bytes=payload.storage_quota_bytes,
            region=payload.region,
        )
        return TenantQuotaOut(**quota)

    @router.get(
        "/api/admin/tenants/{tenant_id}/quota",
        response_model=TenantQuotaOut,
    )
    def get_tenant_quota_route(
        principal: Annotated[Principal, Depends(require_permission("tenant:manage"))],
        tenant_id: str,
    ) -> TenantQuotaOut:
        # Phase 32.1 audit: quota routes take a tenant id from the path but the
        # caller must only manage their own tenant.  ``_ensure_same_tenant``
        # guards against a tenant admin mutating another tenant's quota even
        # when they hold ``tenant:manage``.
        _ensure_same_tenant(principal, tenant_id)
        try:
            return TenantQuotaOut(**database.get_tenant_quota(tenant_id))
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.put(
        "/api/admin/tenants/{tenant_id}/quota",
        response_model=TenantQuotaOut,
    )
    def set_tenant_quota_route(
        principal: Annotated[Principal, Depends(require_permission("tenant:manage"))],
        tenant_id: str,
        payload: TenantQuotaUpdateRequest,
    ) -> TenantQuotaOut:
        _ensure_same_tenant(principal, tenant_id)
        try:
            quota = database.set_tenant_quota(
                tenant_id,
                conversation_quota=payload.conversation_quota,
                storage_quota_bytes=payload.storage_quota_bytes,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return TenantQuotaOut(**quota)

    @router.post(
        "/api/admin/tenants/{tenant_id}/members",
        response_model=TenantMemberOut,
        status_code=201,
    )
    def invite_member_route(
        tenant_id: str,
        payload: MemberInviteRequest,
        principal: Annotated[Principal, Depends(require_permission("tenant:manage"))],
    ) -> TenantMemberOut:
        """Invite a member to a tenant (Phase 22.2). Idempotent per member."""
        _ensure_same_tenant(principal, tenant_id)
        # W1: re-inviting sets status to ``invited``, so an active admin who is
        # re-invited would stop satisfying the active-admin requirement; never
        # let that leave the tenant with zero active admins.
        _guard_keeps_an_admin(database.list_members(tenant_id), payload.actor_id)
        try:
            member = database.invite_member(
                tenant_id, payload.actor_id, payload.role, principal.actor_id
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        # Phase 41.3 SEC-005: membership is a high-risk family; the audit event
        # and its chain tip anchor atomically or the invite fails closed.
        audit_high_risk(
            database,
            tenant_id=tenant_id,
            conversation_id=None,
            actor=principal.actor_id,
            event_type="member.invited",
            payload={
                "member_id": payload.actor_id,
                "role": payload.role,
            },
            reason="member invited",
        )
        return TenantMemberOut(**member)

    @router.get(
        "/api/admin/tenants/{tenant_id}/members",
        response_model=list[TenantMemberOut],
    )
    def list_members_route(
        tenant_id: str,
        principal: Annotated[Principal, Depends(require_permission("tenant:manage"))],
    ) -> list[TenantMemberOut]:
        _ensure_same_tenant(principal, tenant_id)
        return [TenantMemberOut(**m) for m in database.list_members(tenant_id)]

    @router.patch(
        "/api/admin/tenants/{tenant_id}/members/{actor_id}",
        response_model=TenantMemberOut,
    )
    def update_member_role_route(
        tenant_id: str,
        actor_id: str,
        payload: MemberRoleUpdateRequest,
        principal: Annotated[Principal, Depends(require_permission("tenant:manage"))],
    ) -> TenantMemberOut:
        _ensure_same_tenant(principal, tenant_id)
        # Phase 32.1 audit (self-deactivation guard): a member must not be able
        # to demote themselves and lose ``tenant:manage``.  ROADMAP §17.3 does
        # not require this but the absence is a real safety gap — the last
        # admin could lock the tenant out.
        if actor_id == principal.actor_id and payload.role not in {"admin"}:
            raise HTTPException(
                status_code=409,
                detail="Cannot demote yourself: at least one admin member is required",
            )
        # W1: symmetric last-admin guard — a demotion must not strip the
        # tenant of its final active admin, even when the caller is a
        # credential-scoped admin outside ``tenant_members``.
        if payload.role != "admin":
            _guard_keeps_an_admin(database.list_members(tenant_id), actor_id)
        try:
            member = database.update_member_role(
                tenant_id, actor_id, payload.role, principal.actor_id
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        audit_high_risk(
            database,
            tenant_id=tenant_id,
            conversation_id=None,
            actor=principal.actor_id,
            event_type="member.role_updated",
            payload={
                "member_id": actor_id,
                "role": payload.role,
            },
            reason="member role updated",
        )
        return TenantMemberOut(**member)

    @router.post(
        "/api/admin/tenants/{tenant_id}/members/{actor_id}/deactivate",
        response_model=TenantMemberOut,
    )
    def deactivate_member_route(
        tenant_id: str,
        actor_id: str,
        principal: Annotated[Principal, Depends(require_permission("tenant:manage"))],
    ) -> TenantMemberOut:
        """Deactivate a member; history and audit events are retained (22.2)."""
        _ensure_same_tenant(principal, tenant_id)
        # Phase 32.1 audit (self-deactivation guard): an admin must not be able
        # to deactivate their own membership and lock the tenant out.
        if actor_id == principal.actor_id:
            raise HTTPException(
                status_code=409,
                detail="Cannot deactivate yourself: assign another admin first",
            )
        # W1: deactivating must leave at least one active admin behind, even
        # when the caller is a credential-scoped admin outside the roster.
        _guard_keeps_an_admin(database.list_members(tenant_id), actor_id)
        try:
            member = database.deactivate_member(tenant_id, actor_id, principal.actor_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        audit_high_risk(
            database,
            tenant_id=tenant_id,
            conversation_id=None,
            actor=principal.actor_id,
            event_type="member.deactivated",
            payload={"member_id": actor_id},
            reason="member deactivated",
        )
        return TenantMemberOut(**member)

    @router.get(
        "/api/admin/usage",
        response_model=list[TenantUsageRowOut],
    )
    def export_tenant_usage_route(
        principal: Annotated[Principal, Depends(require_permission("tenant:manage"))],
        tenant_id: Annotated[str | None, Query()] = None,
        since: Annotated[str | None, Query(max_length=10)] = None,
        until: Annotated[str | None, Query(max_length=10)] = None,
        limit: int = 100,
    ) -> list[TenantUsageRowOut]:
        """Raw daily usage for billing (Phase 22.4).

        ``tenant_id`` defaults to the caller's tenant; a system admin may
        pass any tenant id.
        """
        scope = tenant_id or principal.tenant_id
        if scope != principal.tenant_id and not principal.can("tenant:manage"):
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return [
            TenantUsageRowOut(**row)
            for row in database.list_tenant_usage(scope, since=since, until=until, limit=limit)
        ]

    # ------------------------------------------------------------------
    # Data governance: retention policies and data-subject requests
    # ------------------------------------------------------------------

    @router.get("/api/retention/policies")
    def list_retention_policies(
        principal: Annotated[Principal, Depends(require_permission("metrics:read"))],
    ) -> list[dict[str, Any]]:
        assert services.retention_service is not None
        return services.retention_service.get_retention_policies(principal.tenant_id)

    @router.put("/api/retention/policies/{data_type}", status_code=200)
    def set_retention_policy(
        principal: Annotated[Principal, Depends(require_permission("metrics:read"))],
        data_type: str,
        retention_days: int,
    ) -> dict[str, Any]:
        assert services.retention_service is not None
        return services.retention_service.set_retention_policy(
            principal.tenant_id, data_type, retention_days, principal.actor_id
        )

    @router.post("/api/retention/enforce")
    def enforce_retention(
        principal: Annotated[Principal, Depends(require_permission("metrics:read"))],
    ) -> dict[str, int]:
        assert services.retention_service is not None
        return services.retention_service.enforce_all(principal.tenant_id)

    # ------------------------------------------------------------------
    # Data-subject requests (M0 SEC-002): create -> approve -> execute.
    # Only privacy-permission holders can act; execution is by request id
    # (never a raw customer reference) and deletion uses maker-checker
    # separation inside retention service.
    # ------------------------------------------------------------------

    @router.post("/api/data-subject-requests", status_code=201)
    def create_data_subject_request(
        payload: DsrCreateRequest,
        principal: Annotated[Principal, Depends(require_permission("privacy:request"))],
    ) -> dict[str, Any]:
        assert services.data_protection is not None
        try:
            return services.data_protection.create_data_subject_request(
                principal.tenant_id,
                payload.customer_ref,
                payload.request_type,
                principal.actor_id,
                payload.idempotency_key,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/data-subject-requests")
    def list_data_subject_requests(
        principal: Annotated[Principal, Depends(require_permission("privacy:request"))],
    ) -> list[dict[str, Any]]:
        assert services.data_protection is not None
        return services.data_protection.list_approved_pending(principal.tenant_id)

    @router.post("/api/data-subject-requests/{request_id}/approve")
    def approve_data_subject_request(
        principal: Annotated[Principal, Depends(require_permission("privacy:approve"))],
        request_id: str,
    ) -> dict[str, Any]:
        assert services.retention_service is not None
        try:
            return services.retention_service.approve_data_subject_request(
                principal.tenant_id, request_id, principal.actor_id
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/api/data-subject-requests/{request_id}/execute")
    def execute_data_subject_request(
        principal: Annotated[Principal, Depends(require_permission("privacy:execute"))],
        request_id: str,
    ) -> dict[str, Any]:
        assert services.data_protection is not None
        try:
            return services.data_protection.execute_data_subject_request(
                principal.tenant_id, request_id, principal.actor_id
            )
        except DsrExportError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.public_message) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/data-subject-requests/{request_id}/export")
    def download_data_subject_export(
        principal: Annotated[Principal, Depends(require_permission("privacy:execute"))],
        request_id: str,
        token: str,
    ) -> dict[str, Any]:
        assert services.retention_service is not None
        try:
            payload = services.retention_service.download_data_subject_export(
                principal.tenant_id, request_id, token
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if payload is None:
            raise HTTPException(status_code=404, detail="Export not found or token expired")
        return payload

    # ------------------------------------------------------------------
    # Privacy operations console (Phase 41.4 DATA): approval-board SLA
    # posture, deletion-proof attestations, failure retry, and breach scan.
    # ------------------------------------------------------------------

    @router.get("/api/privacy/board")
    def privacy_approval_board(
        principal: Annotated[Principal, Depends(require_permission("privacy:manage"))],
    ) -> list[dict[str, Any]]:
        assert services.data_protection is not None
        return services.data_protection.list_approved_pending(principal.tenant_id)

    @router.post("/api/privacy/sla/scan")
    def privacy_flag_sla_breaches(
        principal: Annotated[Principal, Depends(require_permission("privacy:manage"))],
    ) -> dict[str, int]:
        assert services.data_protection is not None
        return {"flagged": services.data_protection.flag_sla_breaches(principal.tenant_id)}

    @router.post("/api/privacy/requests/{request_id}/retry")
    def privacy_retry_failed_request(
        principal: Annotated[Principal, Depends(require_permission("privacy:manage"))],
        request_id: str,
    ) -> dict[str, Any]:
        assert services.data_protection is not None
        try:
            return services.data_protection.retry_failed_request(principal.tenant_id, request_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/privacy/deletion-proof")
    def privacy_deletion_proof(
        principal: Annotated[Principal, Depends(require_permission("privacy:manage"))],
        customer_ref: str,
        secret: str,
    ) -> dict[str, Any]:
        assert services.data_protection is not None
        proof = services.data_protection.deletion_proof(principal.tenant_id, customer_ref, secret)
        if proof is None:
            raise HTTPException(status_code=404, detail="No matching deletion proof")
        return proof

    @router.get("/api/privacy/tombstones")
    def privacy_list_tombstones(
        principal: Annotated[Principal, Depends(require_permission("privacy:manage"))],
    ) -> list[dict[str, Any]]:
        assert services.data_protection is not None
        return services.data_protection.list_tombstones(principal.tenant_id)

    # ------------------------------------------------------------------
    # Outbound webhooks (Phase 20.5): tenant endpoint registration and
    # delivery history. Registration and removal are admin-only and audited;
    # every call is tenant-bound so tenants cannot see or touch each other's
    # endpoints or deliveries.
    # ------------------------------------------------------------------

    @router.post("/api/webhooks", response_model=WebhookOut, status_code=201)
    def register_webhook(
        payload: WebhookCreateRequest,
        principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
    ) -> WebhookOut:
        assert services.webhooks is not None
        try:
            endpoint = services.webhooks.register_endpoint(
                principal.tenant_id, payload.url, payload.events, payload.secret
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except EnvelopeCryptoError as exc:
            # ROADMAP 43.2 fail-closed: envelope encryption is required but the
            # cipher could not boot. Surface the safe public message with the
            # cipher's status code instead of a bare 500.
            raise HTTPException(status_code=exc.status_code, detail=exc.public_message) from exc
        audit_high_risk(
            database,
            tenant_id=principal.tenant_id,
            conversation_id=None,
            actor=principal.actor_id,
            event_type="webhook.registered",
            payload={"endpoint_id": endpoint["id"], "url": payload.url, "events": payload.events},
            reason="webhook registered",
        )
        return WebhookOut(**endpoint)

    @router.get("/api/webhooks", response_model=list[WebhookOut])
    def list_webhooks(
        principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
    ) -> list[WebhookOut]:
        assert services.webhooks is not None
        return [
            WebhookOut(**endpoint)
            for endpoint in services.webhooks.list_endpoints(principal.tenant_id)
        ]

    @router.delete("/api/webhooks/{endpoint_id}", status_code=204)
    def delete_webhook(
        endpoint_id: str,
        principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
    ) -> None:
        assert services.webhooks is not None
        deleted = services.webhooks.delete_endpoint(principal.tenant_id, endpoint_id)
        if not deleted:
            raise LookupError("Webhook endpoint not found")
        audit_high_risk(
            database,
            tenant_id=principal.tenant_id,
            conversation_id=None,
            actor=principal.actor_id,
            event_type="webhook.deleted",
            payload={"endpoint_id": endpoint_id},
            reason="webhook deleted",
        )

    @router.get("/api/webhooks/deliveries", response_model=list[WebhookDeliveryOut])
    def list_webhook_deliveries(
        principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
        endpoint_id: Annotated[str | None, Query(max_length=64)] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> list[WebhookDeliveryOut]:
        assert services.webhooks is not None
        deliveries = services.webhooks.list_deliveries(
            principal.tenant_id, endpoint_id, limit=limit
        )
        return [WebhookDeliveryOut(**delivery) for delivery in deliveries]

    @router.post("/api/admin/keys/{credential_id}/revoke", status_code=200)
    def revoke_api_key_route(
        credential_id: str,
        principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
    ) -> dict[str, Any]:
        """Revoke an API key by its credential id (Phase 28.2 / 41 SEC-004).

        The credential id is the sha256[:12] of the key, exposed via
        ``GET /api/me`` (``credential_id``). Phase 41 routes the revocation
        through the credential registry — the persistent arbiter — so it takes
        effect immediately on every instance without a restart; the legacy
        revoked-keys table and in-memory set are kept in sync so an older peer
        still honours the revoke during the mixed-version window.
        """
        if deps.services.credential_lifecycle is not None:
            try:
                deps.services.credential_lifecycle.revoke(
                    credential_id, now=utc_now(), by=principal.actor_id
                )
            except LookupError:
                raise HTTPException(
                    status_code=404, detail="Credential not found in registry"
                ) from None
            except InvalidCredentialTransition:
                raise HTTPException(
                    status_code=409, detail="Credential cannot be revoked in its current state"
                ) from None
        database.revoke_api_key(credential_id, principal.actor_id)
        deps.services.authenticator.revoke(credential_id)
        audit_high_risk(
            database,
            tenant_id=principal.tenant_id,
            conversation_id=None,
            actor=principal.actor_id,
            event_type="api_key.revoked",
            payload={"credential_id": credential_id},
            reason="api key revoked",
        )
        return {"credential_id": credential_id, "revoked": True}

    # ------------------------------------------------------- auto routing (backlog)

    @router.get("/api/admin/agent-groups", response_model=list[AgentGroupOut])
    def list_agent_groups(
        principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
    ) -> list[AgentGroupOut]:
        return [AgentGroupOut(**group) for group in database.list_agent_groups(principal.tenant_id)]

    @router.post("/api/admin/agent-groups", response_model=AgentGroupOut, status_code=201)
    def create_agent_group(
        payload: AgentGroupCreateRequest,
        principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
    ) -> AgentGroupOut:
        group = database.create_agent_group(
            principal.tenant_id, payload.name, payload.skills, payload.capacity
        )
        database.audit(
            principal.tenant_id,
            None,
            principal.actor_id,
            "agent_group.created",
            {"group_id": group["id"], "name": group["name"]},
        )
        return AgentGroupOut(**group)

    @router.delete("/api/admin/agent-groups/{group_id}", status_code=204)
    def delete_agent_group(
        group_id: str,
        principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
    ) -> None:
        if not database.delete_agent_group(principal.tenant_id, group_id):
            raise HTTPException(status_code=404, detail="Agent group not found")

    @router.post(
        "/api/admin/agent-groups/{group_id}/agents",
        response_model=AgentGroupMemberOut,
        status_code=201,
    )
    def add_group_agent(
        group_id: str,
        payload: dict[str, Any],
        principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
    ) -> AgentGroupMemberOut:
        actor_id = payload.get("actor_id")
        if not isinstance(actor_id, str) or not actor_id:
            raise HTTPException(status_code=422, detail="actor_id is required")
        member = database.add_group_agent(principal.tenant_id, group_id, actor_id)
        if member is None:
            raise HTTPException(status_code=404, detail="Agent group not found")
        database.audit(
            principal.tenant_id,
            None,
            principal.actor_id,
            "agent_group.member_added",
            {"group_id": group_id, "actor_id": actor_id},
        )
        return AgentGroupMemberOut(
            group_id=group_id,
            tenant_id=principal.tenant_id,
            actor_id=actor_id,
            added_at=member.get("added_at") or "",
        )

    @router.delete("/api/admin/agent-groups/{group_id}/agents/{actor_id}", status_code=204)
    def remove_group_agent(
        group_id: str,
        actor_id: str,
        principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
    ) -> None:
        if not database.remove_group_agent(principal.tenant_id, group_id, actor_id):
            raise HTTPException(status_code=404, detail="Member not found")

    @router.get("/api/admin/routing-rules", response_model=list[RoutingRuleOut])
    def list_routing_rules(
        principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
    ) -> list[RoutingRuleOut]:
        return [RoutingRuleOut(**rule) for rule in database.list_routing_rules(principal.tenant_id)]

    @router.post("/api/admin/routing-rules", response_model=RoutingRuleOut, status_code=201)
    def create_routing_rule(
        payload: RoutingRuleCreateRequest,
        principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
    ) -> RoutingRuleOut:
        try:
            rule = database.create_routing_rule(
                principal.tenant_id,
                intent=payload.intent,
                label=payload.label,
                channel=payload.channel,
                group_id=payload.group_id,
                priority=payload.priority,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        database.audit(
            principal.tenant_id,
            None,
            principal.actor_id,
            "routing_rule.created",
            {"rule_id": rule["id"], "group_id": rule["group_id"]},
        )
        return RoutingRuleOut(**rule)

    @router.delete("/api/admin/routing-rules/{rule_id}", status_code=204)
    def delete_routing_rule(
        rule_id: str,
        principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
    ) -> None:
        if not database.delete_routing_rule(principal.tenant_id, rule_id):
            raise HTTPException(status_code=404, detail="Routing rule not found")

    # ------------------------------------------------------------ CSAT summary

    @router.get("/api/admin/csat-summary", response_model=CsatSummaryOut)
    def csat_summary(
        principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
        days: Annotated[int, Query(ge=1, le=90)] = 14,
    ) -> CsatSummaryOut:
        """Aggregate answered CSAT surveys for the admin「评分汇总」card."""
        return CsatSummaryOut(**database.summarize_csat(principal.tenant_id, days))

    # ------------------------------------------------------------ SLA policies

    @router.get("/api/admin/sla-policies", response_model=list[SlaPolicyOut])
    def list_sla_policies(
        principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
    ) -> list[SlaPolicyOut]:
        policies = database.list_sla_policies(principal.tenant_id)
        return [SlaPolicyOut(**policy) for policy in policies]

    @router.put("/api/admin/sla-policies", response_model=SlaPolicyOut)
    def set_sla_policy(
        payload: SlaPolicyRequest,
        principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
    ) -> SlaPolicyOut:
        # Tenant-scoped policies are bound to the caller's tenant; only the
        # global default (tenant_id NULL) is cross-tenant by design. Explicitly
        # targeting another tenant is refused even with admin:manage.
        if payload.tenant_id is not None and payload.tenant_id != principal.tenant_id:
            raise HTTPException(status_code=403, detail="Cannot manage another tenant's policy")
        tenant_id = payload.tenant_id or principal.tenant_id
        policy = database.set_sla_policy(
            tenant_id=tenant_id,
            priority=payload.priority,
            channel=payload.channel,
            first_response_minutes=payload.first_response_minutes,
            resolve_minutes=payload.resolve_minutes,
        )
        audit_high_risk(
            database,
            tenant_id=tenant_id,
            conversation_id=None,
            actor=principal.actor_id,
            event_type="sla_policy.set",
            payload={"policy_id": policy["id"], "priority": policy["priority"]},
            reason="sla policy set",
        )
        return SlaPolicyOut(**policy)

    # ------------------------------------------------------------------
    # Audit evidence: external anchors + observable gaps (Phase 41.3 SEC-005)
    # ------------------------------------------------------------------

    @router.get("/api/admin/audit/anchors")
    def list_audit_anchors(
        principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
    ) -> list[dict[str, Any]]:
        """List in-DB frontier anchors (mind the read-only gate)."""
        with database.connect() as connection:
            rows = connection.execute(
                "SELECT anchor_id, seq, chain_hash, event_type, reason, created_at "
                "FROM audit_anchors ORDER BY seq DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    @router.post("/api/admin/audit/anchors/verify")
    def verify_audit_anchors_route(
        principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
    ) -> dict[str, Any]:
        """Re-verify every external anchor against the local chain (41.3)."""
        anchor_service = services.anchor_service
        if anchor_service is None:
            raise HTTPException(status_code=503, detail="Anchor service unavailable")
        return anchor_service.verify_anchors()

    @router.get("/api/admin/audit/gaps")
    def list_audit_gaps(
        principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
    ) -> list[dict[str, Any]]:
        """List observable audit_gap rows merged with in-process counts."""
        tracker = services.gap_tracker
        local = tracker.local_counts if tracker is not None else {}
        try:
            rows = list_gaps(database)
        except Exception:
            logger.exception("audit_gap.list_failed")
            rows = []
        return merge_gap_rows(rows, local)

    # ------------------------------------------------------- credential rotation
    #
    # Phase 41 / SEC-004 — runtime API-key lifecycle. Issuing returns the raw
    # secret exactly once; every later read (list, detail, audit) carries only
    # the deterministic credential id and the key fingerprint. The key is
    # registered as an active credential immediately so it can be revoked
    # anywhere, and the caller promotes it into the deployment's key
    # configuration to start signing (the two-step rotation drill in 41.1).

    @router.post("/api/admin/keys", response_model=dict[str, str], status_code=201)
    def issue_runtime_api_key(
        principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
    ) -> dict[str, str]:
        """Issue a fresh runtime API key (41.1 rotation drill).

        The secret is returned once in this response and is never stored — the
        database keeps only ``sha256(secret)[:12]`` as the credential id and the
        full fingerprint.  The id is the same deterministic one a re-seed of the
        deployment config would produce for the key, so promoting the issued
        key into ``API_KEYS_JSON`` (the second rotation step) keeps the same
        registry row and revocations address it by id immediately.
        """
        store = services.credential_store
        if store is None:
            raise HTTPException(status_code=503, detail="Credential registry unavailable")
        api_key = issue_api_key()
        credential_id = key_ref_for(api_key)[:API_KEY_CREDENTIAL_ID_PREFIX_LENGTH]
        now = utc_now()
        store.register(
            credential_id=credential_id,
            type="api_key",
            tenant_id=principal.tenant_id,
            key_ref=key_ref_for(api_key),
            not_before="",
            expires_at=None,
            version=1,
            created_at=now,
            status=CredentialStatus.ACTIVE.value,
        )
        audit_high_risk(
            database,
            tenant_id=principal.tenant_id,
            conversation_id=None,
            actor=principal.actor_id,
            event_type="api_key.issued",
            payload={"credential_id": credential_id, "type": "api_key"},
            reason="runtime api key issued",
        )
        return {"credential_id": credential_id, "secret": api_key}

    @router.get("/api/admin/keys", response_model=list[dict[str, Any]])
    def list_api_keys(
        principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
    ) -> list[dict[str, Any]]:
        """List the tenant's API-key credentials; never returns a secret."""
        store = services.credential_store
        if store is None:
            raise HTTPException(status_code=503, detail="Credential registry unavailable")
        rows = store.list_for_tenant(principal.tenant_id, type="api_key")
        return [
            {
                "credential_id": row["credential_id"],
                "type": row["type"],
                "tenant_id": row["tenant_id"],
                "status": row["status"],
                "version": row["version"],
                "key_ref": row["key_ref"],
                "not_before": row["not_before"],
                "expires_at": row["expires_at"],
                "last_used_at": row["last_used_at"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "rotation_of": row["rotation_of"],
            }
            for row in rows
        ]

    return router
