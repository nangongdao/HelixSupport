"""Provider adapter SDK (ROADMAP 42.5 / REL-003).

Vendors speak their own wire format; the Helix core speaks exactly one:
the Phase 38 unified channel webhook (``POST /api/channels/{account_id}/webhook``)
with its receipt idempotency, durable thread mapping, and turn-job
idempotency. A :class:`ProviderAdapter` is the only vendor-specific code —
it authenticates the provider's request and normalizes it into a
:class:`NormalizedEvent`; everything downstream stays core.

The reference adapter pins the *reference wire contract* any new provider
adapter can be diffed against:

- signature: HMAC-SHA256 hex over ``"<unix_timestamp>.<body>"`` in headers
  ``X-Helix-Timestamp`` / ``X-Helix-Signature`` (identical to the core's own
  inbound contract — one verification rule everywhere);
- payload: JSON with ``message_id`` / ``thread_id`` / ``customer_id`` /
  ``customer_name`` / ``content`` and an optional ``kind``
  (``message|edit|recall|receipt``) plus ``attachments`` references;
- unknown kinds fail closed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from typing import Mapping, Protocol

from app.channel_webhooks import ChannelWebhookAuthError

# Replay window for the timestamp header, mirroring the core ingress default.
DEFAULT_REPLAY_WINDOW_SECONDS = 300

EVENT_KINDS = ("message", "edit", "recall", "receipt")

TIMESTAMP_HEADER = "X-Helix-Timestamp"
SIGNATURE_HEADER = "X-Helix-Signature"


@dataclass(frozen=True)
class AttachmentRef:
    """A provider-side attachment reference (never inline bytes)."""

    external_id: str
    filename: str
    content_type: str
    url: str


@dataclass(frozen=True)
class NormalizedEvent:
    """The single event shape every provider adapter must produce."""

    kind: str
    external_message_id: str
    thread_id: str
    customer_id: str
    customer_name: str | None
    content: str
    attachments: tuple[AttachmentRef, ...] = field(default_factory=tuple)
    occurred_at: str | None = None


class ProviderAdapter(Protocol):
    """The whole vendor-specific surface."""

    name: str

    def verify_signature(self, secret: bytes, headers: Mapping[str, str], body: bytes) -> None:
        """Raise ChannelWebhookAuthError unless the request authenticates."""
        ...

    def parse(self, body: bytes) -> NormalizedEvent:
        """Normalize one delivery body; raise ValueError on malformed input."""
        ...


def verify_reference_signature(
    secret: bytes,
    headers: Mapping[str, str],
    body: bytes,
    *,
    replay_window_seconds: int = DEFAULT_REPLAY_WINDOW_SECONDS,
    now_epoch: int | None = None,
) -> None:
    """Core signing rule: fresh timestamp + exact-body HMAC, fail closed."""
    raw_timestamp = headers.get(TIMESTAMP_HEADER, "")
    raw_signature = headers.get(SIGNATURE_HEADER, "")
    if not raw_timestamp or not raw_signature:
        raise ChannelWebhookAuthError("missing signature headers")
    try:
        timestamp = int(raw_timestamp)
    except ValueError as exc:
        raise ChannelWebhookAuthError("malformed signature timestamp") from exc
    now = now_epoch if now_epoch is not None else int(time.time())
    if abs(now - timestamp) > replay_window_seconds:
        raise ChannelWebhookAuthError("signature timestamp outside replay window")
    expected = hmac.new(secret, f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    supplied = raw_signature.strip()
    # The core ingress pins the ``sha256=<hex>`` scheme; accept exactly that.
    if supplied.lower().startswith("sha256="):
        supplied = supplied[7:]
    if not hmac.compare_digest(expected, supplied.lower()):
        raise ChannelWebhookAuthError("invalid signature")


class ReferenceJsonAdapter:
    """Reference implementation of the provider wire contract."""

    name = "reference"

    def __init__(self, replay_window_seconds: int = DEFAULT_REPLAY_WINDOW_SECONDS) -> None:
        self.replay_window_seconds = replay_window_seconds

    def verify_signature(self, secret: bytes, headers: Mapping[str, str], body: bytes) -> None:
        verify_reference_signature(
            secret, headers, body, replay_window_seconds=self.replay_window_seconds
        )

    def parse(self, body: bytes) -> NormalizedEvent:
        try:
            document = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"provider payload is not valid JSON: {exc}") from exc
        if not isinstance(document, dict):
            raise ValueError("provider payload must be a JSON object")
        kind = str(document.get("kind") or "message").strip().lower()
        if kind not in EVENT_KINDS:
            raise ValueError(f"unknown provider event kind: {kind!r}")
        message_id = str(document.get("message_id") or "").strip()
        thread_id = str(document.get("thread_id") or "").strip()
        customer_id = str(document.get("customer_id") or "").strip()
        if not message_id or len(message_id) > 160:
            raise ValueError("provider payload requires a message_id (<=160 chars)")
        if not thread_id or len(thread_id) > 160:
            raise ValueError("provider payload requires a thread_id (<=160 chars)")
        if not customer_id or len(customer_id) > 120:
            raise ValueError("provider payload requires a customer_id (<=120 chars)")
        attachments = []
        for raw in document.get("attachments") or []:
            if not isinstance(raw, dict):
                raise ValueError("attachment references must be objects")
            attachments.append(
                AttachmentRef(
                    external_id=str(raw.get("id") or ""),
                    filename=str(raw.get("filename") or "attachment"),
                    content_type=str(raw.get("content_type") or "application/octet-stream"),
                    url=str(raw.get("url") or ""),
                )
            )
        occurred_at = document.get("occurred_at")
        return NormalizedEvent(
            kind=kind,
            external_message_id=message_id,
            thread_id=thread_id,
            customer_id=customer_id,
            customer_name=(
                str(document["customer_name"]) if document.get("customer_name") else None
            ),
            content=str(document.get("content") or ""),
            attachments=tuple(attachments),
            occurred_at=str(occurred_at) if occurred_at else None,
        )


PROVIDER_ADAPTERS: dict[str, ProviderAdapter] = {
    ReferenceJsonAdapter.name: ReferenceJsonAdapter(),
}


def get_provider_adapter(name: str) -> ProviderAdapter:
    try:
        return PROVIDER_ADAPTERS[name]
    except KeyError as exc:
        raise ValueError(f"unknown provider adapter: {name!r}") from exc


def sign_reference_request(
    secret: bytes, body: bytes, *, now_epoch: int | None = None
) -> dict[str, str]:
    """Produce the reference signature headers (used by the conformance suite)."""
    timestamp = now_epoch if now_epoch is not None else int(time.time())
    signature = hmac.new(secret, f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return {
        TIMESTAMP_HEADER: str(timestamp),
        SIGNATURE_HEADER: f"sha256={signature}",
    }
