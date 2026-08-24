"""Outbox consumer: drain domain events into webhook delivery (ROADMAP 43.3).

``DomainEventOutbox.record`` is write-side only — business rows and their
domain events commit together (transactional outbox), but nothing consumes
the events yet. This is the consumer side: ``WebhookEventConsumer`` claims
unpublished events oldest-first and fans each one out to every endpoint
subscribed to its type via ``WebhookService.emit_event``.

The ``webhook_deliveries (endpoint_id, event_id)`` unique constraint is the
consumer-side deduplication id — an event replayed across racing workers, or
re-claimed after a handler failure, re-enqueues no duplicate delivery. An
event with no active subscriber is still marked published: delivery is
at-least-once with consumer-side dedup, and nothing retries an event that no
endpoint wants.
"""

from __future__ import annotations

import logging
from typing import Any

from app.event_outbox import DomainEventOutbox
from app.webhooks import EVENT_CONVERSATION_CREATED

logger = logging.getLogger("helix")

# Domain event type (schema registry name, ``helix.*``) -> subscribable webhook
# type. The outbox records versioned domain events; webhook endpoints subscribe
# with the un-namespaced event name (``EVENT_CONVERSATION_CREATED``). An event
# type with no entry here still publishes but fans out to no endpoint.
WEBHOOK_TYPE_BY_DOMAIN_EVENT: dict[str, str] = {
    "helix.conversation.created": EVENT_CONVERSATION_CREATED,
}


class WebhookEventConsumer:
    """Drain outbox events and fan them out to subscribed webhook endpoints."""

    def __init__(self, database: Any, webhook_service: Any) -> None:
        self.database = database
        self.webhook_service = webhook_service
        self._outbox = DomainEventOutbox(database)

    def drain_and_deliver(self, *, limit: int = 100) -> dict[str, int]:
        """Publish up to ``limit`` events, fanning each out to webhooks.

        Returns:
            - ``published``: events claimed and handed to the handler;
            - ``deliveries``: webhook_deliveries rows enqueued;
            - ``delivered``: events that reached at least one active endpoint;
            - ``no_endpoint``: events drained with no active subscriber.
        """
        deliveries = 0
        delivered = 0
        no_endpoint = 0

        def handler(event: dict[str, Any]) -> None:
            nonlocal deliveries, delivered, no_endpoint
            webhook_type = WEBHOOK_TYPE_BY_DOMAIN_EVENT.get(event["event_type"])
            if webhook_type is None:
                # A domain event with no subscribable webhook type: it still
                # publishes (at-least-once), but no endpoint can want it.
                no_endpoint += 1
                return
            enqueued = self.webhook_service.emit_event(
                event["tenant_id"],
                webhook_type,
                event["payload"],
                event["event_id"],
            )
            deliveries += enqueued
            if enqueued:
                delivered += 1
            else:
                no_endpoint += 1

        published = self._outbox.drain(handler, limit=limit)
        return {
            "published": published,
            "deliveries": deliveries,
            "delivered": delivered,
            "no_endpoint": no_endpoint,
        }

    def pending_count(self) -> int:
        """Unpublished events still waiting on a drain."""
        return self._outbox.pending_count()
