"""Outbound webhook delivery (Phase 20.5).

Tenants register endpoints subscribing to events (conversation created,
escalated to human, resolved, SLA breached). ``emit_event`` creates a
delivery row per matching endpoint, deduplicated by
``(endpoint_id, event_id)`` so a consumer that retries can recognize
already-delivered events. ``deliver_pending`` POSTs each due delivery with
an HMAC-SHA256 signature, retries transient failures with exponential
back-off, and moves deliveries past ``max_attempts`` to ``dead``.

This is at-least-once delivery plus a consumer-deduplication id; the
``event_id`` is the deduplication key.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx

from app.database import Database, utc_now
from app.envelope_crypto import EnvelopeCipherProtocol, EnvelopeCryptoError
from app.webhook_safety import HostResolver, assert_public_webhook_url

logger = logging.getLogger(__name__)

# A delivery transport returns (status_code, error_or_none). 0 status means a
# network/timeout failure (retryable). Injectable so tests never hit the network.
DeliveryTransport = Callable[[str, dict[str, str], bytes, float], tuple[int, str | None]]

# Subscribable event types (Phase 20.5).
EVENT_CONVERSATION_CREATED = "conversation.created"
EVENT_CONVERSATION_ESCALATED = "conversation.escalated"
EVENT_CONVERSATION_RESOLVED = "conversation.resolved"
EVENT_CONVERSATION_SLA_BREACHED = "conversation.sla_breached"
EVENT_CONVERSATION_SLA_IMPENDING = "conversation.sla_impending"
EVENT_REPORT_GENERATED = "report.generated"
SUPPORTED_EVENTS = frozenset(
    {
        EVENT_CONVERSATION_CREATED,
        EVENT_CONVERSATION_ESCALATED,
        EVENT_CONVERSATION_RESOLVED,
        EVENT_CONVERSATION_SLA_BREACHED,
        EVENT_CONVERSATION_SLA_IMPENDING,
        EVENT_REPORT_GENERATED,
    }
)

# SLA breach deliveries are deduplicated per conversation by fixing the event
# id, so repeated scans never re-enqueue the same breach to the same endpoint.
_SLA_BREACH_EVENT_ID_PREFIX = "sla_breach:"

# A delivery stuck in ``sending`` (a crashed instance claimed it) is re-claimed
# after this many seconds so the queue drains without manual intervention.
SENDING_LEASE_SECONDS = 60.0


def _default_transport(
    url: str, headers: dict[str, str], body: bytes, timeout: float
) -> tuple[int, str | None]:
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(url, headers=headers, content=body)
    except httpx.HTTPError as exc:
        return 0, str(exc)
    return response.status_code, None


def _sign(secret: str, timestamp: str, body: bytes) -> str:
    """HMAC-SHA256 over ``timestamp.body`` -- the shared secret lets the
    consumer verify the request came from this service and was not replayed."""
    message = f"{timestamp}.".encode() + body
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class WebhookConfig:
    timeout_seconds: float = 10.0
    max_attempts: int = 5
    base_backoff_seconds: float = 1.0
    retryable_status_codes: tuple[int, ...] = (429, 500, 502, 503, 504)


class WebhookService:
    """Register endpoints, emit events, and deliver pending webhook payloads."""

    def __init__(
        self,
        database: Database,
        *,
        config: WebhookConfig | None = None,
        transport: DeliveryTransport | None = None,
        resolve_host: HostResolver | None = None,
        envelope_cipher: EnvelopeCipherProtocol | None = None,
        envelope_required: bool = False,
    ) -> None:
        self.database = database
        self.config = config or WebhookConfig()
        self._transport = transport or _default_transport
        self._resolve_host = resolve_host
        # ROADMAP 43.2: when the envelope feature is enabled but the cipher is
        # unavailable, restricted fields must not silently fall back to
        # plaintext -- registration is refused instead (fail closed).
        self.envelope_cipher = envelope_cipher
        self.envelope_required = envelope_required

    # ------------------------------------------------------------------ endpoints

    def register_endpoint(
        self,
        tenant_id: str,
        url: str,
        events: list[str],
        secret: str,
    ) -> dict[str, Any]:
        assert_public_webhook_url(url, resolve_host=self._resolve_host)
        if not events:
            raise ValueError("webhook must subscribe to at least one event")
        unsupported = set(events) - SUPPORTED_EVENTS
        if unsupported:
            raise ValueError(f"unsupported webhook events: {sorted(unsupported)}")
        if not secret:
            raise ValueError("webhook secret is required")
        endpoint_id = str(uuid4())
        now = utc_now()
        if self.envelope_cipher is not None:
            stored_secret = json.dumps(
                self.envelope_cipher.encrypt_text(tenant_id, secret), sort_keys=True
            )
            secret_format = "envelope"
        elif self.envelope_required:
            # ROADMAP 43.2 fail-closed: envelope encryption is mandated for
            # webhook secrets but the cipher could not boot -- refuse rather
            # than silently store plaintext.
            raise EnvelopeCryptoError(
                "envelope encryption is required but unavailable", status_code=503
            )
        else:
            stored_secret = secret
            secret_format = "plain"
        with self.database.connect() as conn:
            conn.execute(
                "INSERT INTO webhook_endpoints"
                "(id, tenant_id, url, secret, secret_format, events_json, status, "
                "created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)",
                (
                    endpoint_id,
                    tenant_id,
                    url,
                    stored_secret,
                    secret_format,
                    json.dumps(events),
                    now,
                    now,
                ),
            )
        return {
            "id": endpoint_id,
            "tenant_id": tenant_id,
            "url": url,
            "events": events,
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }

    def list_endpoints(self, tenant_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as conn:
            rows = conn.execute(
                "SELECT id, tenant_id, url, events_json, status, created_at, updated_at "
                "FROM webhook_endpoints WHERE tenant_id = ? ORDER BY created_at",
                (tenant_id,),
            ).fetchall()
        return [self._endpoint_row(r) for r in rows]

    def delete_endpoint(self, tenant_id: str, endpoint_id: str) -> bool:
        with self.database.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM webhook_endpoints WHERE id = ? AND tenant_id = ?",
                (endpoint_id, tenant_id),
            )
            if cursor.rowcount == 0:
                return False
            # Orphaned deliveries can never be delivered (their endpoint is
            # gone); dead-letter them so they do not sit in ``pending``
            # forever and history stays truthful.
            updated = utc_now()
            conn.execute(
                "UPDATE webhook_deliveries SET status='dead', last_error='endpoint deleted', "
                "updated_at=? WHERE endpoint_id = ? AND tenant_id = ? "
                "AND status IN ('pending', 'sending')",
                (updated, endpoint_id, tenant_id),
            )
            return True

    @staticmethod
    def _endpoint_row(row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "tenant_id": row["tenant_id"],
            "url": row["url"],
            "events": json.loads(row["events_json"]),
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    # ------------------------------------------------------------------ emit

    def emit_event(
        self,
        tenant_id: str,
        event_type: str,
        payload: dict[str, Any],
        event_id: str,
    ) -> int:
        """Enqueue deliveries for every active endpoint subscribing to the event.

        Returns the number of new deliveries created. The ``(endpoint_id,
        event_id)`` unique constraint means re-emitting the same event_id to the
        same endpoint is a no-op -- the consumer-side deduplication id.
        """
        now = utc_now()
        with self.database.connect() as conn:
            rows = conn.execute(
                "SELECT id, events_json FROM webhook_endpoints "
                "WHERE tenant_id = ? AND status = 'active'",
                (tenant_id,),
            ).fetchall()
            enqueued = 0
            for row in rows:
                if event_type not in json.loads(row["events_json"]):
                    continue
                endpoint_id = row["id"]
                delivery_id = str(uuid4())
                cursor = conn.execute(
                    "INSERT INTO webhook_deliveries"
                    "(id, tenant_id, endpoint_id, event_type, event_id, payload_json, "
                    " status, attempts, max_attempts, next_attempt_at, last_response_code, "
                    " last_error, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?, NULL, NULL, ?, ?) "
                    "ON CONFLICT(endpoint_id, event_id) DO NOTHING",
                    (
                        delivery_id,
                        tenant_id,
                        endpoint_id,
                        event_type,
                        event_id,
                        json.dumps(payload, ensure_ascii=False),
                        self.config.max_attempts,
                        now,
                        now,
                        now,
                    ),
                )
                if cursor.rowcount:
                    enqueued += 1
        return enqueued

    def emit_event_to_endpoint(
        self,
        tenant_id: str,
        endpoint_id: str,
        event_type: str,
        payload: dict[str, Any],
        event_id: str,
    ) -> int:
        """Enqueue one delivery to a specific endpoint (report delivery).

        Backlog (报表导出与订阅): scheduled reports target the subscription's
        endpoint regardless of the endpoint's event subscriptions, so the
        scan does not leak a report to every ``report.generated`` subscriber.
        The ``(endpoint_id, event_id)`` constraint keeps re-scans idempotent.
        """
        now = utc_now()
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT id FROM webhook_endpoints WHERE tenant_id = ? AND id = ? "
                "AND status = 'active'",
                (tenant_id, endpoint_id),
            ).fetchone()
            if row is None:
                raise LookupError("Webhook endpoint not found")
            delivery_id = str(uuid4())
            cursor = conn.execute(
                "INSERT INTO webhook_deliveries"
                "(id, tenant_id, endpoint_id, event_type, event_id, payload_json, "
                " status, attempts, max_attempts, next_attempt_at, last_response_code, "
                " last_error, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?, NULL, NULL, ?, ?) "
                "ON CONFLICT(endpoint_id, event_id) DO NOTHING",
                (
                    delivery_id,
                    tenant_id,
                    endpoint_id,
                    event_type,
                    event_id,
                    json.dumps(payload, ensure_ascii=False),
                    self.config.max_attempts,
                    now,
                    now,
                    now,
                ),
            )
        return 1 if cursor.rowcount else 0

    # ------------------------------------------------------------------ sla

    def check_sla_breaches(self) -> int:
        """Emit ``conversation.sla_breached`` for every overdue conversation.

        Idempotent: the event id is ``sla_breach:{conversation_id}``, so the
        ``(endpoint_id, event_id)`` unique constraint turns repeated scans
        into no-ops once an endpoint has a delivery for that conversation.
        Returns the number of deliveries newly enqueued.
        """
        now = utc_now()
        with self.database.connect() as conn:
            rows = conn.execute(
                "SELECT id, tenant_id, customer_name, status, priority, sla_due_at "
                "FROM conversations "
                "WHERE status != 'resolved' AND sla_due_at IS NOT NULL AND sla_due_at < ?",
                (now,),
            ).fetchall()
        emitted = 0
        for row in rows:
            payload = {
                "conversation_id": row["id"],
                "customer_name": row["customer_name"],
                "status": row["status"],
                "priority": row["priority"],
                "sla_due_at": row["sla_due_at"],
            }
            # The event id carries the breach deadline so a *new* breach
            # episode (the conversation was resolved, reopened with a fresh
            # SLA window, and breached again) is a distinct event. Repeated
            # scans of the same breach keep the same id and stay deduplicated.
            emitted += self.emit_event(
                row["tenant_id"],
                EVENT_CONVERSATION_SLA_BREACHED,
                payload,
                f"{_SLA_BREACH_EVENT_ID_PREFIX}{row['id']}:{row['sla_due_at']}",
            )
        return emitted

    def check_sla_impending(self, *, window_minutes: int = 5) -> int:
        """Emit ``conversation.sla_impending`` for conversations due soon.

        Pre-breach warning (backlog): fires for conversations whose
        ``sla_due_at`` is within ``window_minutes`` in the future but not yet
        passed. Idempotent per (conversation, due) via the event id, so the
        warning is sent once per SLA window.
        """
        now = datetime.now(UTC)
        horizon = (now + timedelta(minutes=window_minutes)).isoformat(timespec="microseconds")
        now_text = now.isoformat(timespec="microseconds")
        with self.database.connect() as conn:
            rows = conn.execute(
                "SELECT id, tenant_id, customer_name, status, priority, sla_due_at "
                "FROM conversations "
                "WHERE status != 'resolved' AND sla_due_at IS NOT NULL "
                "AND sla_due_at > ? AND sla_due_at <= ?",
                (now_text, horizon),
            ).fetchall()
        emitted = 0
        for row in rows:
            payload = {
                "conversation_id": row["id"],
                "customer_name": row["customer_name"],
                "status": row["status"],
                "priority": row["priority"],
                "sla_due_at": row["sla_due_at"],
                "impending": True,
            }
            emitted += self.emit_event(
                row["tenant_id"],
                EVENT_CONVERSATION_SLA_IMPENDING,
                payload,
                f"sla_impending:{row['id']}:{row['sla_due_at']}",
            )
        return emitted

    # ------------------------------------------------------------------ deliver

    def deliver_pending(self, *, limit: int = 100) -> dict[str, int]:
        """Deliver due webhooks: claim, sign, POST, retry or dead-letter.

        Each due row is atomically claimed (``pending`` -> ``sending``) in its
        own committed transaction before the HTTP POST, so concurrent worker
        instances cannot POST the same delivery twice; a row stuck in
        ``sending`` (the claiming instance crashed) is re-claimed once its
        lease has expired. Claim, POST, and outcome are separate transactions
        so a failure on one delivery cannot roll back deliveries that already
        succeeded.
        """
        now = utc_now()
        lease_floor = (datetime.now(UTC) - timedelta(seconds=SENDING_LEASE_SECONDS)).isoformat(
            timespec="microseconds"
        )
        results = {"delivered": 0, "retried": 0, "dead": 0}
        with self.database.connect() as conn:
            rows = conn.execute(
                "SELECT d.id, d.tenant_id, d.endpoint_id, d.event_type, d.event_id, "
                "       d.payload_json, d.attempts, d.max_attempts, e.url, e.secret, "
                "       e.secret_format "
                "FROM webhook_deliveries d "
                "JOIN webhook_endpoints e ON d.endpoint_id = e.id "
                "WHERE (d.status = 'pending' "
                "   OR (d.status = 'sending' AND d.updated_at < ?)) "
                "  AND d.next_attempt_at <= ? "
                "ORDER BY d.next_attempt_at LIMIT ?",
                (lease_floor, now, limit),
            ).fetchall()
        for row in rows:
            # Claim in a committed transaction so the lease is durable; the
            # loser of a concurrent claim sees status='sending' and skips.
            with self.database.connect() as conn:
                claimed = conn.execute(
                    "UPDATE webhook_deliveries SET status='sending', updated_at=? "
                    "WHERE id=? AND "
                    "(status = 'pending' OR (status = 'sending' AND updated_at < ?))",
                    (now, row["id"], lease_floor),
                ).rowcount
            if not claimed:
                # Another instance claimed this delivery while we were
                # reading; let it finish.
                continue
            try:
                self._deliver_one(row, results)
            except Exception:
                # One bad delivery must not abort the batch; it is already
                # claimed and will be re-claimed after the lease expires.
                logger.exception("webhook delivery failed", extra={"delivery_id": row["id"]})
        return results

    def _resolve_secret(self, row: Any) -> str | None:
        """Return the plaintext signing secret for a delivery row.

        Envelope-encrypted secrets (``secret_format='envelope'``) are decrypted
        with the tenant's DEK (ROADMAP 43.2). A decryption failure returns
        ``None`` so the caller dead-letters the delivery -- we never sign with
        a secret that could not be verified (fail closed).
        """
        if row["secret_format"] == "envelope":
            if self.envelope_cipher is None:
                return None
            try:
                envelope = json.loads(row["secret"])
                return self.envelope_cipher.decrypt_text(row["tenant_id"], envelope)
            except (EnvelopeCryptoError, ValueError, TypeError, KeyError, AttributeError):
                return None
        return row["secret"]

    def _deliver_one(self, row: Any, results: dict[str, int]) -> None:
        try:
            assert_public_webhook_url(row["url"], resolve_host=self._resolve_host)
        except ValueError as exc:
            updated = utc_now()
            with self.database.connect() as conn:
                conn.execute(
                    "UPDATE webhook_deliveries SET status='dead', attempts=?, "
                    "last_response_code=NULL, last_error=?, updated_at=? WHERE id=?",
                    (int(row["attempts"]) + 1, str(exc), updated, row["id"]),
                )
            results["dead"] += 1
            return
        secret = self._resolve_secret(row)
        if secret is None:
            updated = utc_now()
            with self.database.connect() as conn:
                conn.execute(
                    "UPDATE webhook_deliveries SET status='dead', attempts=?, "
                    "last_response_code=NULL, last_error=?, updated_at=? WHERE id=?",
                    (
                        int(row["attempts"]) + 1,
                        "envelope secret could not be decrypted",
                        updated,
                        row["id"],
                    ),
                )
            results["dead"] += 1
            return
        body = json.dumps(
            {
                "event_type": row["event_type"],
                "event_id": row["event_id"],
                "payload": json.loads(row["payload_json"]),
            },
            ensure_ascii=False,
        ).encode("utf-8")
        timestamp = str(int(time.time()))
        signature = _sign(secret, timestamp, body)
        headers = {
            "Content-Type": "application/json",
            "X-Helix-Timestamp": timestamp,
            "X-Helix-Signature": signature,
        }
        status, err = self._transport(row["url"], headers, body, self.config.timeout_seconds)
        attempts = int(row["attempts"]) + 1
        updated = utc_now()
        if status and 200 <= status < 300:
            with self.database.connect() as conn:
                conn.execute(
                    "UPDATE webhook_deliveries SET status='delivered', attempts=?, "
                    "last_response_code=?, last_error=NULL, updated_at=? WHERE id=?",
                    (attempts, status, updated, row["id"]),
                )
            results["delivered"] += 1
            return
        # A permanent consumer error (4xx outside the retryable set) means
        # retrying will never succeed; dead-letter immediately instead of
        # burning the remaining attempts.
        if status and status not in self.config.retryable_status_codes:
            with self.database.connect() as conn:
                conn.execute(
                    "UPDATE webhook_deliveries SET status='dead', attempts=?, "
                    "last_response_code=?, last_error=?, updated_at=? WHERE id=?",
                    (attempts, status, err or f"HTTP {status}", updated, row["id"]),
                )
            results["dead"] += 1
            return
        if attempts >= int(row["max_attempts"]):
            with self.database.connect() as conn:
                conn.execute(
                    "UPDATE webhook_deliveries SET status='dead', attempts=?, "
                    "last_response_code=?, last_error=?, updated_at=? WHERE id=?",
                    (attempts, status or 0, err or f"HTTP {status}", updated, row["id"]),
                )
            results["dead"] += 1
            return
        # Exponential back-off with jitter so retrying deliveries do not all
        # wake at the same instant (a fleet-wide thundering herd).
        backoff = self.config.base_backoff_seconds * (2 ** (attempts - 1))
        backoff *= random.uniform(0.5, 1.5)
        next_at = (datetime.now(UTC) + timedelta(seconds=backoff)).isoformat(
            timespec="microseconds"
        )
        # Release the sending lease so the next due attempt can be claimed.
        # A crashed worker still recovers via SENDING_LEASE_SECONDS.
        with self.database.connect() as conn:
            conn.execute(
                "UPDATE webhook_deliveries SET status='pending', attempts=?, "
                "last_response_code=?, last_error=?, next_attempt_at=?, updated_at=? "
                "WHERE id=?",
                (attempts, status or 0, err, next_at, updated, row["id"]),
            )
        results["retried"] += 1

    # ------------------------------------------------------------------ history

    def list_deliveries(
        self,
        tenant_id: str,
        endpoint_id: str | None = None,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        with self.database.connect() as conn:
            if endpoint_id:
                rows = conn.execute(
                    "SELECT id, tenant_id, endpoint_id, event_type, event_id, status, "
                    "attempts, max_attempts, next_attempt_at, last_response_code, "
                    "last_error, created_at, updated_at "
                    "FROM webhook_deliveries WHERE tenant_id = ? AND endpoint_id = ? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (tenant_id, endpoint_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, tenant_id, endpoint_id, event_type, event_id, status, "
                    "attempts, max_attempts, next_attempt_at, last_response_code, "
                    "last_error, created_at, updated_at "
                    "FROM webhook_deliveries WHERE tenant_id = ? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (tenant_id, limit),
                ).fetchall()
        return [dict(row) for row in rows]
