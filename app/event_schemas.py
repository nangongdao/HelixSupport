"""Domain event schema registry (ROADMAP 43.3).

Every outbox event type is registered with a schema version and a field
map. Evolution rules are enforced at registration time:

- **BACKWARD** (default): a new version may add optional fields and may
  narrow nothing — every field the previous version had must still exist
  with the same type. Old consumers can read new events.
- **FORWARD**: new consumers can read old events — fields present in the
  new version must have existed before, additions are forbidden.

Removing or re-typing a field under BACKWARD compatibility raises at
registration, so a silent semantic change fails in CI instead of in a
consumer at 3am.
"""

from __future__ import annotations

from dataclasses import dataclass

_COMPAT_MODES = ("backward", "forward")


class SchemaCompatibilityError(ValueError):
    """An event schema evolution violates its declared compatibility mode."""


@dataclass(frozen=True)
class EventSchema:
    name: str
    version: int
    # field name -> JSON type name ("string" | "int" | "float" | "bool" |
    # "object" | "string|null" …); free-form but compared for equality.
    fields: dict[str, str]
    compatibility: str = "backward"
    description: str = ""


_REGISTRY: dict[str, list[EventSchema]] = {}


def register_event_schema(schema: EventSchema) -> EventSchema:
    """Register one schema version; validates evolution against the latest."""
    if schema.compatibility not in _COMPAT_MODES:
        raise SchemaCompatibilityError(
            f"event {schema.name}: unknown compatibility mode {schema.compatibility!r}"
        )
    history = _REGISTRY.setdefault(schema.name, [])
    if history:
        latest = history[-1]
        if schema.version <= latest.version:
            raise SchemaCompatibilityError(
                f"event {schema.name}: version {schema.version} does not advance {latest.version}"
            )
        _check_compatible(latest, schema)
    history.append(schema)
    return schema


def _check_compatible(old: EventSchema, new: EventSchema) -> None:
    if new.compatibility == "backward":
        removed = sorted(set(old.fields) - set(new.fields))
        if removed:
            raise SchemaCompatibilityError(
                f"event {new.name} v{new.version}: backward-incompatible field removal {removed}"
            )
        retyped = {
            name
            for name, kind in old.fields.items()
            if name in new.fields and new.fields[name] != kind
        }
        if retyped:
            raise SchemaCompatibilityError(
                f"event {new.name} v{new.version}: backward-incompatible type "
                f"change {sorted(retyped)}"
            )
    else:  # forward
        added = sorted(set(new.fields) - set(old.fields))
        if added:
            raise SchemaCompatibilityError(
                f"event {new.name} v{new.version}: forward-incompatible field addition {added}"
            )


def latest_event_schema(name: str) -> EventSchema:
    history = _REGISTRY.get(name)
    if not history:
        raise KeyError(f"unknown event schema: {name!r}")
    return history[-1]


def all_event_schemas() -> dict[str, list[EventSchema]]:
    return {name: list(versions) for name, versions in _REGISTRY.items()}


# ---------------------------------------------------------------------------
# The shipped event catalogue. v1 payloads are frozen; evolution goes through
# new versions registered here with explicit compatibility.
# ---------------------------------------------------------------------------

register_event_schema(
    EventSchema(
        name="helix.conversation.created",
        version=1,
        fields={
            "conversation_id": "string",
            "channel": "string",
            "customer_name": "string",
            "customer_verified": "bool",
            "source_api": "string",
        },
        description="A conversation was created (v2 ingress records it transactionally).",
    )
)
