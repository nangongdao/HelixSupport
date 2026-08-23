"""Tests for the prompt version registry (Phase 19.1)."""

import tempfile
import unittest
from pathlib import Path

from app.database import Database
from app.migrations import all_migrations, run_migrations
from app.prompts import PromptRegistry


class PromptRegistryTests(unittest.TestCase):
    """Test prompt version lifecycle management."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.database = Database(self.db_path, pool_size=2)

        with self.database.connect() as conn:
            run_migrations(conn, all_migrations())

        self.database.ensure_tenant("tenant-1", "Test Tenant")
        self.registry = PromptRegistry(self.database)

    def tearDown(self) -> None:
        self.database.close()
        self.temp_dir.cleanup()

    def test_create_draft_version(self) -> None:
        """New prompt versions start in draft status."""
        version = self.registry.create_version(
            tenant_id="tenant-1",
            name="triage_prompt",
            version="v1.0",
            body="Classify the customer message",
            model_ref="gpt-4o",
            actor_id="admin-1",
        )

        self.assertEqual(version.tenant_id, "tenant-1")
        self.assertEqual(version.name, "triage_prompt")
        self.assertEqual(version.version, "v1.0")
        self.assertEqual(version.status, "draft")
        self.assertIsNone(version.activated_at)

    def test_activate_version(self) -> None:
        """Activating a version marks it active and retires previous active."""
        v1 = self.registry.create_version(
            "tenant-1", "triage_prompt", "v1.0", "Body v1", "gpt-4o", "admin-1"
        )
        self.registry.activate("tenant-1", v1.id, "admin-1")

        v2 = self.registry.create_version(
            "tenant-1", "triage_prompt", "v1.1", "Body v1.1", "gpt-4o", "admin-1"
        )
        activated = self.registry.activate("tenant-1", v2.id, "admin-1")

        self.assertEqual(activated.status, "active")
        self.assertIsNotNone(activated.activated_at)

        # v1 should now be retired
        v1_after = self.registry.get_version("tenant-1", v1.id)
        assert v1_after is not None
        self.assertEqual(v1_after.status, "retired")

    def test_canary_deployment(self) -> None:
        """Setting a canary version clears any previous canary."""
        v1 = self.registry.create_version(
            "tenant-1", "triage_prompt", "v1.0", "Body v1", "gpt-4o", "admin-1"
        )
        canary = self.registry.set_canary("tenant-1", v1.id, "admin-1")

        self.assertEqual(canary.status, "canary")

        # Set a new canary
        v2 = self.registry.create_version(
            "tenant-1", "triage_prompt", "v2.0", "Body v2", "gpt-4o", "admin-1"
        )
        self.registry.set_canary("tenant-1", v2.id, "admin-1")

        # v1 should revert to draft
        v1_after = self.registry.get_version("tenant-1", v1.id)
        assert v1_after is not None
        self.assertEqual(v1_after.status, "draft")

    def test_rollback_to_previous_active(self) -> None:
        """Rollback reactivates the most recently retired version."""
        v1 = self.registry.create_version(
            "tenant-1", "triage_prompt", "v1.0", "Body v1", "gpt-4o", "admin-1"
        )
        self.registry.activate("tenant-1", v1.id, "admin-1")

        v2 = self.registry.create_version(
            "tenant-1", "triage_prompt", "v1.1", "Body v1.1", "gpt-4o", "admin-1"
        )
        self.registry.activate("tenant-1", v2.id, "admin-1")

        # Rollback should reactivate v1
        rolled_back = self.registry.rollback("tenant-1", "triage_prompt", "admin-1")

        assert rolled_back is not None
        self.assertEqual(rolled_back.id, v1.id)
        self.assertEqual(rolled_back.status, "active")

        # v2 should now be retired
        v2_after = self.registry.get_version("tenant-1", v2.id)
        assert v2_after is not None
        self.assertEqual(v2_after.status, "retired")

    def test_rollback_with_no_history(self) -> None:
        """Rollback returns None when no retired version exists."""
        result = self.registry.rollback("tenant-1", "nonexistent_prompt", "admin-1")
        self.assertIsNone(result)

    def test_get_active_prompt(self) -> None:
        """Retrieve the currently active prompt for a given name."""
        v1 = self.registry.create_version(
            "tenant-1", "triage_prompt", "v1.0", "Body v1", "gpt-4o", "admin-1"
        )
        self.registry.activate("tenant-1", v1.id, "admin-1")

        active = self.registry.get_active_prompt("tenant-1", "triage_prompt")
        assert active is not None
        self.assertEqual(active.id, v1.id)
        self.assertEqual(active.status, "active")

    def test_get_canary_prompt(self) -> None:
        """Retrieve the canary prompt for a given name."""
        v1 = self.registry.create_version(
            "tenant-1", "triage_prompt", "v1.0", "Body v1", "gpt-4o", "admin-1"
        )
        self.registry.set_canary("tenant-1", v1.id, "admin-1")

        canary = self.registry.get_canary_prompt("tenant-1", "triage_prompt")
        assert canary is not None
        self.assertEqual(canary.id, v1.id)
        self.assertEqual(canary.status, "canary")

    def test_list_versions_by_name(self) -> None:
        """List all versions of a specific prompt name."""
        self.registry.create_version(
            "tenant-1", "triage_prompt", "v1.0", "Body v1", "gpt-4o", "admin-1"
        )
        self.registry.create_version(
            "tenant-1", "triage_prompt", "v1.1", "Body v1.1", "gpt-4o", "admin-1"
        )
        self.registry.create_version(
            "tenant-1", "policy_prompt", "v1.0", "Different prompt", "gpt-4o", "admin-1"
        )

        versions = self.registry.list_versions("tenant-1", name="triage_prompt")
        self.assertEqual(len(versions), 2)
        self.assertTrue(all(v.name == "triage_prompt" for v in versions))

    def test_global_prompts(self) -> None:
        """Global prompts (tenant_id=None) are supported."""
        global_v = self.registry.create_version(
            tenant_id=None,
            name="global_prompt",
            version="v1.0",
            body="Global body",
            model_ref="gpt-4o",
            actor_id="system",
        )

        self.assertIsNone(global_v.tenant_id)
        retrieved = self.registry.get_version(None, global_v.id)
        assert retrieved is not None
        self.assertEqual(retrieved.name, "global_prompt")

    def test_tenant_isolation(self) -> None:
        """Prompts are isolated by tenant."""
        self.database.ensure_tenant("tenant-2", "Tenant Two")

        v1 = self.registry.create_version(
            "tenant-1", "shared_name", "v1.0", "Body 1", "gpt-4o", "admin-1"
        )
        v2 = self.registry.create_version(
            "tenant-2", "shared_name", "v1.0", "Body 2", "gpt-4o", "admin-2"
        )

        # Each tenant sees only their own version
        tenant1_versions = self.registry.list_versions("tenant-1", name="shared_name")
        tenant2_versions = self.registry.list_versions("tenant-2", name="shared_name")

        self.assertEqual(len(tenant1_versions), 1)
        self.assertEqual(tenant1_versions[0].id, v1.id)

        self.assertEqual(len(tenant2_versions), 1)
        self.assertEqual(tenant2_versions[0].id, v2.id)

    def test_lifecycle_audits_participate_in_hash_chain(self) -> None:
        from app.audit_chain import verify_chain
        from scripts.verify_audit_chain import load_rows

        version = self.registry.create_version(
            "tenant-1", "chain_prompt", "v1", "Body", "gpt-4o", "admin-1"
        )
        self.registry.activate("tenant-1", version.id, "admin-1")
        self.database.audit("tenant-1", None, "admin-1", "after.prompt", {})

        with self.database.connect() as connection:
            rows = load_rows(connection)
        self.assertEqual(
            [row["event_type"] for row in rows],
            ["prompt_version.created", "prompt_version.activated", "after.prompt"],
        )
        self.assertTrue(all(row["event_hash"] for row in rows))
        self.assertEqual(verify_chain(rows), [])


if __name__ == "__main__":
    unittest.main()
