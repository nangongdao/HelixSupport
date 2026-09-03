"""Signed formal-channel inbound webhook reference (ROADMAP Phase 23.3)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Request, Response

from app.channel_webhooks import (
    ChannelWebhookAuthError,
    InboundChannelAccount,
    channel_message_key,
    content_sha256,
)
from app.context import bind_tenant_scope
from app.domain import ConversationStatus
from app.intake import backpressure_reason
from app.main import AppServices, _conversation_quota_exceeded
from app.orchestrator import IdempotencyConflictError, InvalidTransitionError
from app.schemas import ChannelWebhookAccepted, ChannelWebhookMessageRequest


router = APIRouter(prefix="/api/channels", tags=["channels"])
MAX_WEBHOOK_BODY_BYTES = 16 * 1024


def _services(request: Request) -> AppServices:
    services: AppServices | None = getattr(request.app.state, "services", None)
    if services is None:
        raise HTTPException(status_code=500, detail="services not initialized")
    return services


async def _authenticate_channel(
    request: Request,
    account_id: Annotated[
        str,
        Path(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$"),
    ],
    x_helix_timestamp: Annotated[str | None, Header(alias="X-Helix-Timestamp")] = None,
    x_helix_signature: Annotated[str | None, Header(alias="X-Helix-Signature")] = None,
    x_helix_key_id: Annotated[str | None, Header(alias="X-Helix-Key-Id")] = None,
) -> InboundChannelAccount:
    body = await request.body()
    if len(body) > MAX_WEBHOOK_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Webhook payload too large")
    try:
        return _services(request).inbound_channels.authenticate(
            account_id,
            x_helix_timestamp,
            x_helix_signature,
            body,
            key_id=x_helix_key_id,
        )
    except ChannelWebhookAuthError as exc:
        raise HTTPException(
            status_code=401,
            detail="Invalid webhook authentication",
            headers={"WWW-Authenticate": "HMAC"},
        ) from exc


def _validate_receipt(
    receipt: dict[str, Any], payload: ChannelWebhookMessageRequest, body_sha256: str
) -> None:
    if (
        receipt["external_thread_id"] != payload.thread_id
        or receipt["content_sha256"] != body_sha256
    ):
        raise IdempotencyConflictError(
            "Channel message id was already used with a different message"
        )


def _validate_customer(conversation: dict[str, Any], customer_id: str) -> None:
    if conversation.get("customer_ref") != customer_id:
        raise IdempotencyConflictError("Channel thread is already bound to a different customer")


def _replay_response(
    services: AppServices,
    tenant_id: str,
    conversation_id: str,
    message_id: str,
    content: str,
    idempotency_key: str,
) -> ChannelWebhookAccepted | None:
    database = services.database
    existing_message = database.get_message_by_channel_id(tenant_id, conversation_id, message_id)
    if existing_message is not None and existing_message["content"] != content:
        raise IdempotencyConflictError(
            "Channel message id was already used with a different message"
        )
    job = database.get_turn_job_by_idempotency(tenant_id, conversation_id, idempotency_key)
    if job is not None:
        if job["content"] != content or job.get("channel_message_id") != message_id:
            raise IdempotencyConflictError(
                "Channel message id was already used with a different message"
            )
        return ChannelWebhookAccepted(
            conversation_id=conversation_id,
            job_id=job["id"],
            status=job["status"],
            idempotent_replay=True,
            conversation_created=False,
        )
    if existing_message is not None:
        return ChannelWebhookAccepted(
            conversation_id=conversation_id,
            status="completed",
            idempotent_replay=True,
            conversation_created=False,
        )
    return None


@router.post(
    "/{account_id}/webhook",
    response_model=ChannelWebhookAccepted,
    status_code=202,
)
async def receive_channel_message(
    request: Request,
    response: Response,
    payload: ChannelWebhookMessageRequest,
    account: Annotated[InboundChannelAccount, Depends(_authenticate_channel)],
) -> ChannelWebhookAccepted:
    """Authenticate and durably enqueue one external customer message."""
    services = _services(request)
    database = services.database
    orchestrator = services.orchestrator
    tenant_id = account.tenant_id
    # Phase 43.2 contract (a): the HMAC-verified account is the channel's
    # server-bound identity — bind its tenant as the ambient RLS scope.
    bind_tenant_scope(tenant_id)
    actor = f"channel:{account.account_id}"
    body_hash = content_sha256(payload.content)
    idempotency_key = channel_message_key(account.account_id, payload.message_id)

    receipt = database.get_channel_webhook_receipt(
        tenant_id, account.account_id, payload.message_id
    )
    conversation: dict[str, Any] | None = None
    created = False
    if receipt is not None:
        _validate_receipt(receipt, payload, body_hash)
        conversation = database.get_conversation(tenant_id, receipt["conversation_id"])
        if conversation is None:
            raise RuntimeError("Channel receipt references a missing conversation")
        _validate_customer(conversation, payload.customer_id)
        replay = _replay_response(
            services,
            tenant_id,
            conversation["id"],
            payload.message_id,
            payload.content,
            idempotency_key,
        )
        if replay is not None:
            response.headers["X-Idempotent-Replay"] = "true"
            if replay.job_id:
                response.headers["Location"] = f"/api/turn-jobs/{replay.job_id}"
            return replay
    else:
        conversation = database.get_channel_conversation(
            tenant_id, account.account_id, payload.thread_id
        )
        if conversation is None or conversation["status"] == ConversationStatus.RESOLVED:
            quota_error = _conversation_quota_exceeded(database, tenant_id)
            if quota_error:
                raise HTTPException(
                    status_code=429,
                    detail=quota_error,
                    headers={"Retry-After": "60"},
                )

    overload = backpressure_reason(database, services.settings, tenant_id)
    if overload:
        raise HTTPException(
            status_code=429,
            detail=overload,
            headers={"Retry-After": "30"},
        )

    if conversation is None:
        conversation, created = database.get_or_create_channel_conversation(
            tenant_id,
            account.account_id,
            payload.thread_id,
            payload.customer_name,
            payload.customer_id,
            account.channel,
            actor,
            services.settings.normal_sla_minutes,
        )
    _validate_customer(conversation, payload.customer_id)

    receipt, receipt_created = database.claim_channel_webhook_receipt(
        tenant_id,
        account.account_id,
        payload.message_id,
        payload.thread_id,
        conversation["id"],
        body_hash,
    )
    _validate_receipt(receipt, payload, body_hash)
    if receipt["conversation_id"] != conversation["id"]:
        raise IdempotencyConflictError(
            "Channel message id was already used in a different conversation"
        )
    if not receipt_created:
        replay = _replay_response(
            services,
            tenant_id,
            conversation["id"],
            payload.message_id,
            payload.content,
            idempotency_key,
        )
        if replay is not None:
            response.headers["X-Idempotent-Replay"] = "true"
            if replay.job_id:
                response.headers["Location"] = f"/api/turn-jobs/{replay.job_id}"
            return replay

    if conversation["status"] == ConversationStatus.RESOLVED:
        try:
            conversation = orchestrator.reopen(tenant_id, conversation["id"], actor)
        except InvalidTransitionError:
            refreshed = database.get_conversation(tenant_id, conversation["id"])
            if refreshed is None or refreshed["status"] == ConversationStatus.RESOLVED:
                raise
            conversation = refreshed

    job, replayed = orchestrator.queue_customer_message(
        tenant_id,
        conversation["id"],
        payload.content,
        actor,
        idempotency_key,
        services.settings.turn_job_max_attempts,
        channel_message_id=payload.message_id,
    )
    services.turn_worker.notify()
    response.headers["Location"] = f"/api/turn-jobs/{job['id']}"
    response.headers["X-Idempotent-Replay"] = str(replayed).lower()
    return ChannelWebhookAccepted(
        conversation_id=conversation["id"],
        job_id=job["id"],
        status=job["status"],
        idempotent_replay=replayed,
        conversation_created=created,
    )
