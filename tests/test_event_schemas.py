"""Tests for app/event_schemas.py domain event schema registry."""

import pytest

from app.event_schemas import (
    EventSchema,
    SchemaCompatibilityError,
    all_event_schemas,
    latest_event_schema,
    register_event_schema,
)


def test_register_event_schema_first_version():
    """First version of a schema registers successfully."""
    schema = EventSchema(
        name="test.event.first",
        version=1,
        fields={"field1": "string", "field2": "int"},
    )
    result = register_event_schema(schema)
    assert result == schema
    assert latest_event_schema("test.event.first") == schema


def test_register_event_schema_backward_compatible_addition():
    """Backward-compatible: adding optional fields is allowed."""
    schema_v1 = EventSchema(
        name="test.event.backward_add",
        version=1,
        fields={"field1": "string"},
        compatibility="backward",
    )
    register_event_schema(schema_v1)

    schema_v2 = EventSchema(
        name="test.event.backward_add",
        version=2,
        fields={"field1": "string", "field2": "int"},
        compatibility="backward",
    )
    result = register_event_schema(schema_v2)
    assert result == schema_v2


def test_register_event_schema_backward_incompatible_removal():
    """Backward-incompatible: removing a field raises error."""
    schema_v1 = EventSchema(
        name="test.event.backward_remove",
        version=1,
        fields={"field1": "string", "field2": "int"},
        compatibility="backward",
    )
    register_event_schema(schema_v1)

    schema_v2 = EventSchema(
        name="test.event.backward_remove",
        version=2,
        fields={"field1": "string"},
        compatibility="backward",
    )
    with pytest.raises(SchemaCompatibilityError, match="backward-incompatible field removal"):
        register_event_schema(schema_v2)


def test_register_event_schema_backward_incompatible_type_change():
    """Backward-incompatible: changing field type raises error."""
    schema_v1 = EventSchema(
        name="test.event.backward_type",
        version=1,
        fields={"field1": "string"},
        compatibility="backward",
    )
    register_event_schema(schema_v1)

    schema_v2 = EventSchema(
        name="test.event.backward_type",
        version=2,
        fields={"field1": "int"},
        compatibility="backward",
    )
    with pytest.raises(SchemaCompatibilityError, match="backward-incompatible type change"):
        register_event_schema(schema_v2)


def test_register_event_schema_forward_compatible_no_addition():
    """Forward-compatible: no new fields allowed."""
    schema_v1 = EventSchema(
        name="test.event.forward",
        version=1,
        fields={"field1": "string"},
        compatibility="forward",
    )
    register_event_schema(schema_v1)

    schema_v2 = EventSchema(
        name="test.event.forward",
        version=2,
        fields={"field1": "string", "field2": "int"},
        compatibility="forward",
    )
    with pytest.raises(SchemaCompatibilityError, match="forward-incompatible field addition"):
        register_event_schema(schema_v2)


def test_register_event_schema_version_must_advance():
    """Version must be strictly increasing."""
    schema_v1 = EventSchema(
        name="test.event.version",
        version=2,
        fields={"field1": "string"},
    )
    register_event_schema(schema_v1)

    schema_v2 = EventSchema(
        name="test.event.version",
        version=2,
        fields={"field1": "string"},
    )
    with pytest.raises(SchemaCompatibilityError, match="version 2 does not advance 2"):
        register_event_schema(schema_v2)


def test_register_event_schema_unknown_compatibility_mode():
    """Unknown compatibility mode raises error."""
    schema = EventSchema(
        name="test.event.unknown",
        version=1,
        fields={"field1": "string"},
        compatibility="unknown",
    )
    with pytest.raises(SchemaCompatibilityError, match="unknown compatibility mode"):
        register_event_schema(schema)


def test_latest_event_schema_raises_for_unknown():
    """latest_event_schema raises KeyError for unregistered event."""
    with pytest.raises(KeyError, match="unknown event schema"):
        latest_event_schema("test.event.nonexistent")


def test_all_event_schemas_returns_registry():
    """all_event_schemas returns all registered schemas."""
    schema_v1 = EventSchema(
        name="test.event.all",
        version=1,
        fields={"field1": "string"},
    )
    schema_v2 = EventSchema(
        name="test.event.all",
        version=2,
        fields={"field1": "string", "field2": "int"},
    )
    register_event_schema(schema_v1)
    register_event_schema(schema_v2)

    all_schemas = all_event_schemas()
    assert "test.event.all" in all_schemas
    assert len(all_schemas["test.event.all"]) == 2


def test_event_schema_immutability():
    """EventSchema is frozen and cannot be modified."""
    schema = EventSchema(
        name="test.event.frozen",
        version=1,
        fields={"field1": "string"},
    )
    with pytest.raises(Exception):
        schema.version = 2


def test_helix_conversation_created_registered():
    """The shipped helix.conversation.created event is registered."""
    schema = latest_event_schema("helix.conversation.created")
    assert schema.name == "helix.conversation.created"
    assert schema.version == 1
    assert "conversation_id" in schema.fields
