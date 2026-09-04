"""Phase 43.3: domain event outbox & schema registry.

Contracts: business events commit with their transaction; the drain claims
each row exactly once even across concurrent dispatchers and replays;
consumers dedup by event_id; and the schema registry rejects silent
semantic changes (field removal / retype under BACKWARD compatibility,
addition under FORWARD) while allowing legal evolution.
"""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from app.database import Database
from app.event_outbox import DomainEventOutbox, UnknownEventTypeError
from app.event_schemas import (
    EventSchema,
    SchemaCompatibilityError,
    latest_event_schema,
    register_event_schema,
)


def _seed(db: Database) -> None:
    db.initialize()
    db.ensure_tenant("t1")


class OutboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = Database(Path(self._tmp.name) / "outbox.db")
        _seed(self.db)
        self.outbox = DomainEventOutbox(self.db)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_unknown_event_type_refused(self) -> None:
        with self.assertRaises(UnknownEventTypeError):
            self.outbox.record("t1", "helix.not.a.thing", {})

    def test_payload_missing_fields_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.outbox.record(
                "t1",
                "helix.conversation.created",
                {"conversation_id": "conv_1"},  # channel/customer_name/… missing
            )

    def test_drain_delivers_each_event_exactly_once_across_replays(self) -> None:
        for index in range(5):
            self.outbox.record(
                "t1",
                "helix.conversation.created",
                {
                    "conversation_id": f"conv_{index}",
                    "channel": "web",
                    "customer_name": f"c{index}",
                    "customer_verified": False,
                    "source_api": "v2",
                },
            )
        seen: list[str] = []
        self.assertEqual(self.outbox.drain(lambda event: seen.append(event["event_id"])), 5)
        # Replay delivers nothing new.
        self.assertEqual(self.outbox.drain(lambda event: seen.append(event["event_id"])), 0)
        self.assertEqual(len(set(seen)), len(seen), "duplicate delivery detected")
        self.assertEqual(self.outbox.pending_count(), 0)

    def test_concurrent_dispatchers_never_double_claim(self) -> None:
        for index in range(20):
            self.outbox.record(
                "t1",
                "helix.conversation.created",
                {
                    "conversation_id": f"conv_{index}",
                    "channel": "web",
                    "customer_name": "x",
                    "customer_verified": False,
                    "source_api": "v2",
                },
            )
        delivered: list[str] = []
        lock = threading.Lock()

        def worker() -> None:
            local = []
            self.outbox.drain(lambda event: local.append(event["event_id"]), limit=100)
            with lock:
                delivered.extend(local)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(delivered), 20, "every event delivered exactly once overall")
        self.assertEqual(len(set(delivered)), 20, "no double claim across dispatchers")

    def test_ordering_is_oldest_first_for_replay_safety(self) -> None:
        import time

        ids = []
        for index in range(3):
            ids.append(
                self.outbox.record(
                    "t1",
                    "helix.conversation.created",
                    {
                        "conversation_id": f"conv_{index}",
                        "channel": "web",
                        "customer_name": "x",
                        "customer_verified": False,
                        "source_api": "v2",
                        "seq_marker": index,
                    },
                )
            )
            time.sleep(0.01)
        seen: list[str] = []
        self.outbox.drain(lambda event: seen.append(event["event_id"]))
        self.assertEqual(seen, ids, "drain must publish oldest-first")


class SchemaRegistryTests(unittest.TestCase):
    def test_backward_evolution_allows_additions(self) -> None:
        v1 = register_event_schema(
            EventSchema("test.evolution", 1, {"id": "string", "amount": "int"})
        )
        v2 = register_event_schema(
            EventSchema(
                "test.evolution", 2, {"id": "string", "amount": "int", "currency": "string"}
            )
        )
        self.assertEqual(v1.version, 1)
        self.assertEqual(latest_event_schema("test.evolution").version, 2)
        del v2

    def test_backward_evolution_rejects_field_removal(self) -> None:
        register_event_schema(EventSchema("test.removal", 1, {"id": "string", "note": "string"}))
        with self.assertRaises(SchemaCompatibilityError):
            register_event_schema(EventSchema("test.removal", 2, {"id": "string"}))

    def test_backward_evolution_rejects_retype(self) -> None:
        register_event_schema(EventSchema("test.retype", 1, {"count": "int"}))
        with self.assertRaises(SchemaCompatibilityError):
            register_event_schema(EventSchema("test.retype", 2, {"count": "string"}))

    def test_forward_mode_rejects_additions(self) -> None:
        register_event_schema(
            EventSchema("test.forward", 1, {"id": "string"}, compatibility="forward")
        )
        with self.assertRaises(SchemaCompatibilityError):
            register_event_schema(
                EventSchema(
                    "test.forward", 2, {"id": "string", "extra": "string"}, compatibility="forward"
                )
            )

    def test_non_advancing_version_rejected(self) -> None:
        register_event_schema(EventSchema("test.stale", 1, {"id": "string"}))
        with self.assertRaises(SchemaCompatibilityError):
            register_event_schema(EventSchema("test.stale", 1, {"id": "string"}))


if __name__ == "__main__":
    unittest.main()
