"""Tests for app/outbox_consumer.py webhook event consumer."""

from unittest import mock

from app.outbox_consumer import WEBHOOK_TYPE_BY_DOMAIN_EVENT, WebhookEventConsumer


def test_drain_and_deliver_publishes_to_webhook():
    """Event with webhook mapping gets delivered to webhook_service."""
    db = mock.Mock()
    webhook_service = mock.Mock()
    webhook_service.emit_event.return_value = 1
    consumer = WebhookEventConsumer(db, webhook_service)

    outbox = mock.Mock()
    consumer._outbox = outbox

    def handler_capture(handler, *, limit):
        handler(
            {
                "event_id": "evt_123",
                "event_type": "helix.conversation.created",
                "tenant_id": "tenant-1",
                "payload": {"conversation_id": "conv_456"},
            }
        )
        return 1

    outbox.drain.side_effect = handler_capture

    result = consumer.drain_and_deliver(limit=10)

    assert result["published"] == 1
    assert result["deliveries"] == 1
    assert result["delivered"] == 1
    assert result["no_endpoint"] == 0
    webhook_service.emit_event.assert_called_once_with(
        "tenant-1", "conversation.created", {"conversation_id": "conv_456"}, "evt_123"
    )


def test_drain_and_deliver_no_endpoint_for_unmapped_event():
    """Event with no webhook mapping is marked as no_endpoint."""
    db = mock.Mock()
    webhook_service = mock.Mock()
    consumer = WebhookEventConsumer(db, webhook_service)

    outbox = mock.Mock()
    consumer._outbox = outbox

    def handler_capture(handler, *, limit):
        handler(
            {
                "event_id": "evt_999",
                "event_type": "helix.unknown.event",
                "tenant_id": "tenant-1",
                "payload": {},
            }
        )
        return 1

    outbox.drain.side_effect = handler_capture

    result = consumer.drain_and_deliver(limit=10)

    assert result["published"] == 1
    assert result["deliveries"] == 0
    assert result["delivered"] == 0
    assert result["no_endpoint"] == 1
    webhook_service.emit_event.assert_not_called()


def test_drain_and_deliver_no_active_subscribers():
    """Event with webhook mapping but no active subscribers counts as no_endpoint."""
    db = mock.Mock()
    webhook_service = mock.Mock()
    webhook_service.emit_event.return_value = 0
    consumer = WebhookEventConsumer(db, webhook_service)

    outbox = mock.Mock()
    consumer._outbox = outbox

    def handler_capture(handler, *, limit):
        handler(
            {
                "event_id": "evt_789",
                "event_type": "helix.conversation.created",
                "tenant_id": "tenant-1",
                "payload": {"conversation_id": "conv_999"},
            }
        )
        return 1

    outbox.drain.side_effect = handler_capture

    result = consumer.drain_and_deliver(limit=10)

    assert result["published"] == 1
    assert result["deliveries"] == 0
    assert result["delivered"] == 0
    assert result["no_endpoint"] == 1


def test_drain_and_deliver_multiple_events():
    """Multiple events are processed and counters accumulate."""
    db = mock.Mock()
    webhook_service = mock.Mock()
    webhook_service.emit_event.side_effect = [2, 0, 1]
    consumer = WebhookEventConsumer(db, webhook_service)

    outbox = mock.Mock()
    consumer._outbox = outbox

    def handler_capture(handler, *, limit):
        handler(
            {
                "event_id": "evt_1",
                "event_type": "helix.conversation.created",
                "tenant_id": "tenant-1",
                "payload": {},
            }
        )
        handler(
            {
                "event_id": "evt_2",
                "event_type": "helix.conversation.created",
                "tenant_id": "tenant-1",
                "payload": {},
            }
        )
        handler(
            {
                "event_id": "evt_3",
                "event_type": "helix.conversation.created",
                "tenant_id": "tenant-1",
                "payload": {},
            }
        )
        return 3

    outbox.drain.side_effect = handler_capture

    result = consumer.drain_and_deliver(limit=10)

    assert result["published"] == 3
    assert result["deliveries"] == 3
    assert result["delivered"] == 2
    assert result["no_endpoint"] == 1


def test_pending_count_delegates_to_outbox():
    """pending_count returns the outbox pending count."""
    db = mock.Mock()
    webhook_service = mock.Mock()
    consumer = WebhookEventConsumer(db, webhook_service)

    outbox = mock.Mock()
    outbox.pending_count.return_value = 42
    consumer._outbox = outbox

    assert consumer.pending_count() == 42


def test_webhook_type_mapping_registered():
    """Known domain events are mapped to webhook types."""
    assert WEBHOOK_TYPE_BY_DOMAIN_EVENT["helix.conversation.created"] == "conversation.created"
