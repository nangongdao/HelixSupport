"""Tests for tenant data residency (ROADMAP 43.4).

Covers the vertical slice: migration v40 default, ``region`` through
provisioning and quota reads, backup-manifest residency summaries,
restore-region gating, and the evidence-pack generator.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.control_plane import TenantPolicy
from app.database import Database
from app.residency import (
    REGION_INVENTORY,
    RegionSpec,
    check_restore_compatibility,
    get_region_spec,
    is_known_region,
    resolve_region,
    summarize_tenant_residency,
)
from scripts.backup import backup_database
from scripts.generate_residency_pack import generate_packs
from scripts.restore import restore_database


def region_policy(region: str) -> TenantPolicy:
    return TenantPolicy(plan="standard", region=region)


class MigrationV40Tests(unittest.TestCase):
    """v40 adds tenants.region with a 'local' default; legacy rows keep it."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._tmp.name) / "residency.db")
        self.db.initialize()

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def test_region_column_exists_with_local_default(self) -> None:
        with self.db.connect() as connection:
            columns = {
                row[1]: row for row in connection.execute("PRAGMA table_info(tenants)").fetchall()
            }
        self.assertIn("region", columns)
        self.assertEqual(columns["region"][4], "'local'")

    def test_provisioned_tenant_defaults_to_local(self) -> None:
        self.db.provision_tenant("acme", "Acme", "admin")
        self.assertEqual(self.db.get_tenant_quota("acme")["region"], "local")

    def test_provision_pins_explicit_region(self) -> None:
        self.db.provision_tenant("emea", "Emea", "admin", region="eu-central")
        self.assertEqual(self.db.get_tenant_quota("emea")["region"], "eu-central")

    def test_reprovision_without_region_keeps_pinned_value(self) -> None:
        self.db.provision_tenant("emea", "Emea", "admin", region="eu-central")
        # Idempotent re-run without a region must not reset the pin.
        self.db.provision_tenant("emea", "Emea", "admin", conversation_quota=50)
        self.assertEqual(self.db.get_tenant_quota("emea")["region"], "eu-central")

    def test_reprovision_with_region_updates_pin(self) -> None:
        self.db.provision_tenant("emea", "Emea", "admin", region="eu-central")
        self.db.provision_tenant("emea", "Emea", "admin", region="ap-south")
        self.assertEqual(self.db.get_tenant_quota("emea")["region"], "ap-south")

    def test_provision_audits_region(self) -> None:
        self.db.provision_tenant("audited", "Audited", "admin", region="eu-central")
        with self.db.connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM audit_events "
                "WHERE tenant_id = ? AND event_type = 'tenant.provisioned'",
                ("audited",),
            ).fetchall()
        self.assertTrue(rows)
        payload = json.loads(rows[0]["payload_json"])
        # Residency is part of the audited provisioning record so a
        # cross-border move is never invisible on the audit chain.
        self.assertEqual(payload["name"], "Audited")
        self.assertEqual(payload["region"], "eu-central")


class ResidencySummaryTests(unittest.TestCase):
    """Manifest summaries and restore gating (single-write region)."""

    def test_summary_groups_tenants_by_region(self) -> None:
        rows = [
            {"tenant_id": "a", "region": "local"},
            {"tenant_id": "b", "region": None},
            {"tenant_id": "c", "region": "eu-central"},
        ]
        summary = summarize_tenant_residency(rows)
        self.assertFalse(summary["single_write_region"])
        self.assertIn("local", summary["regions"])
        self.assertIn("eu-central", summary["regions"])
        self.assertEqual(summary["regions"]["local"]["tenant_count"], 2)

    def test_summary_single_write_region_true(self) -> None:
        rows = [{"tenant_id": "a", "region": "eu"}, {"tenant_id": "b", "region": "eu"}]
        self.assertTrue(summarize_tenant_residency(rows)["single_write_region"])

    def test_restore_check_flags_unapproved_region(self) -> None:
        summary = summarize_tenant_residency([{"tenant_id": "a", "region": "eu"}])
        violations = check_restore_compatibility(summary, {"local"})
        self.assertEqual(violations, ["eu"])

    def test_restore_check_accepts_approved_region(self) -> None:
        summary = summarize_tenant_residency([{"tenant_id": "a", "region": "local"}])
        self.assertEqual(check_restore_compatibility(summary, {"local"}), [])

    def test_custom_inventory_extends_specs(self) -> None:
        inventory = dict(REGION_INVENTORY)
        inventory["eu-central"] = RegionSpec(
            name="eu-central",
            storage_location="eu-disk",
            backup_target="eu-bucket",
            cross_border_transfers=(),
        )
        spec = get_region_spec("eu-central", inventory)
        self.assertEqual(spec.storage_location, "eu-disk")

    def test_unknown_region_is_flagged_not_trusted(self) -> None:
        # An unmanaged region name must be detectable everywhere its
        # posture would otherwise silently inherit the local spec.
        self.assertFalse(is_known_region("eu-central"))
        self.assertTrue(is_known_region("local"))
        summary = summarize_tenant_residency([{"tenant_id": "a", "region": "EU-Central"}])
        bucket = summary["regions"]["eu-central"]
        self.assertEqual(bucket["spec_status"], "unknown-fallback-local")

    def test_resolve_region_normalises_case_and_whitespace(self) -> None:
        self.assertEqual(resolve_region("  EU-Central "), "eu-central")
        self.assertEqual(resolve_region(None), "local")
        self.assertEqual(resolve_region(""), "local")


class BackupResidencyTests(unittest.TestCase):
    """backup.py stamps manifests with the covered residency spread."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "bk.db"
        self.db = Database(self.db_path)
        self.db.initialize()
        # initialize() seeds the 'demo' tenant (region 'local'); pin it to
        # eu-central so the whole database shares one residency.
        with self.db.connect() as connection:
            connection.execute("UPDATE tenants SET region = 'eu-central' WHERE id = 'demo'")
        self.db.provision_tenant("acme", "Acme", "admin", region="eu-central")
        self.out_dir = Path(self._tmp.name) / "backups"

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def test_manifest_records_region_spread(self) -> None:
        manifest = backup_database(self.db_path, self.out_dir)
        residency: dict = manifest.get("residency", {})  # type: ignore[assignment]
        self.assertIn("eu-central", residency["regions"])
        tenant_ids: list = residency["regions"]["eu-central"]["tenant_ids"]
        self.assertEqual(tenant_ids, sorted(tenant_ids))
        self.assertIn("acme", tenant_ids)

    def test_restore_blocked_without_target_region(self) -> None:
        manifest = backup_database(self.db_path, self.out_dir)
        backup_file = next(
            path for path in self.out_dir.iterdir() if path.name.endswith((".db", ".db.gz"))
        )
        target = Path(self._tmp.name) / "restored.db"
        with self.assertRaises(RuntimeError) as ctx:
            restore_database(
                backup_file,
                target,
                manifest_path=self.out_dir
                / f"{manifest['backup_file'].rsplit('.', 2)[0]}.manifest.json",
                allowed_regions={"local"},
            )
        self.assertIn("Residency violation", str(ctx.exception))

    def test_restore_allowed_with_matching_region(self) -> None:
        backup_database(self.db_path, self.out_dir)
        backup_file = next(
            path for path in self.out_dir.iterdir() if path.name.endswith((".db", ".db.gz"))
        )
        target = Path(self._tmp.name) / "restored.db"
        restore_database(backup_file, target, allowed_regions={"eu-central"})
        self.assertTrue(target.exists())

    def test_restore_fails_closed_when_manifest_lacks_residency(self) -> None:
        # An explicit allow-list is a hard commitment: a manifest without a
        # residency summary (legacy or hand-edited) must abort the restore
        # instead of silently skipping the region check.
        backup_database(self.db_path, self.out_dir)
        backup_file = next(
            path for path in self.out_dir.iterdir() if path.name.endswith((".db", ".db.gz"))
        )
        stem = backup_file.name.rsplit(".", 2)[0]
        manifest_path = self.out_dir / f"{stem}.manifest.json"
        stripped = json.loads(manifest_path.read_text(encoding="utf-8"))
        del stripped["residency"]
        manifest_path.write_text(json.dumps(stripped), encoding="utf-8")
        with self.assertRaises(RuntimeError) as ctx:
            restore_database(
                backup_file,
                Path(self._tmp.name) / "restored.db",
                manifest_path=manifest_path,
                allowed_regions={"eu-central"},
            )
        self.assertIn("no residency summary", str(ctx.exception))


class EvidencePackTests(unittest.TestCase):
    """generate_residency_pack renders per-tenant packs + the register."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "pack.db"
        self.db = Database(self.db_path)
        self.db.initialize()
        self.db.provision_tenant("acme", "Acme", "admin", region="eu-central")

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def _generate(self, **kwargs: object) -> Path:
        out = Path(self._tmp.name) / "packs"
        generate_packs(self.db_path, out, **kwargs)  # type: ignore[arg-type]
        return out

    def test_pack_contains_residency_block(self) -> None:
        out = self._generate()
        pack = json.loads((out / "acme.residency.json").read_text(encoding="utf-8"))
        self.assertEqual(pack["schema"], "helix.residency.evidence/v1")
        self.assertEqual(pack["residency"]["region"], "eu-central")
        self.assertIn("storage_location", pack["residency"])
        self.assertIn("backup_target", pack["residency"])

    def test_pack_reports_policy_region_mismatch(self) -> None:
        from app.control_plane import TenantControlPlane

        control_plane = TenantControlPlane(self.db, "x" * 32)
        control_plane.set_policy("acme", region_policy("us-east"))
        out = self._generate(signing_secret="x" * 32)
        pack = json.loads((out / "acme.residency.json").read_text(encoding="utf-8"))
        self.assertEqual(pack["control_plane_snapshot"]["status"], "verified")
        self.assertFalse(pack["consistent"])
        self.assertTrue(
            any("us-east" in mismatch for mismatch in pack["mismatches"]),
            pack["mismatches"],
        )

    def test_pack_verified_when_regions_agree(self) -> None:
        from app.control_plane import TenantControlPlane, TenantPolicy

        control_plane = TenantControlPlane(self.db, "x" * 32)
        control_plane.set_policy("acme", TenantPolicy(region="eu-central"))
        out = self._generate(signing_secret="x" * 32)
        pack = json.loads((out / "acme.residency.json").read_text(encoding="utf-8"))
        self.assertEqual(pack["control_plane_snapshot"]["status"], "verified")
        self.assertEqual(pack["mismatches"], [])
        self.assertTrue(pack["consistent"])

    def test_pack_survives_tampered_policy_snapshot(self) -> None:
        # A tampered policy row must be recorded as invalid per tenant —
        # never certified, and never allowed to abort the remaining packs.
        from app.control_plane import TenantControlPlane

        control_plane = TenantControlPlane(self.db, "x" * 32)
        control_plane.set_policy("acme", region_policy("eu-central"))
        with self.db.connect() as connection:
            connection.execute(
                "UPDATE tenant_control_policies SET signature = ? WHERE tenant_id = 'acme'",
                ("0" * 64,),
            )
        out = self._generate(signing_secret="x" * 32)
        pack = json.loads((out / "acme.residency.json").read_text(encoding="utf-8"))
        self.assertEqual(pack["control_plane_snapshot"]["status"], "invalid")
        self.assertFalse(pack["consistent"])
        self.assertTrue(pack["mismatches"])

    def test_pack_flags_unknown_region(self) -> None:
        out = self._generate()
        pack = json.loads((out / "acme.residency.json").read_text(encoding="utf-8"))
        self.assertEqual(pack["residency"]["spec_status"], "unknown-fallback-local")

    def test_cross_border_register_lists_only_open_regions(self) -> None:
        out = self._generate()
        register = json.loads((out / "_cross_border_register.json").read_text(encoding="utf-8"))
        # Default inventory is closed: nothing leaves the region.
        self.assertEqual(register["entries"], [])
        self.assertIn("local", register["regions_in_inventory"])

    def test_backups_dir_correlates_manifests_into_packs(self) -> None:
        backups_dir = Path(self._tmp.name) / "bk"
        backup_database(self.db_path, backups_dir)
        out = self._generate(backups_dir=backups_dir)
        pack = json.loads((out / "acme.residency.json").read_text(encoding="utf-8"))
        self.assertEqual(len(pack["backups_covering_tenant"]), 1)

    def test_legacy_database_without_region_column(self) -> None:
        # Simulate a pre-v40 database: rebuild without the column.
        self.db.close()
        legacy_path = Path(self._tmp.name) / "legacy.db"
        legacy_path.write_bytes(b"")
        import sqlite3

        connection = sqlite3.connect(str(legacy_path))
        connection.execute(
            "CREATE TABLE tenants (id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO tenants VALUES ('old', 'Old', '2020-01-01T00:00:00Z')")
        connection.commit()
        connection.close()
        out = Path(self._tmp.name) / "legacy-packs"
        generate_packs(legacy_path, out)
        pack = json.loads((out / "old.residency.json").read_text(encoding="utf-8"))
        self.assertEqual(pack["residency"]["region"], "local")


if __name__ == "__main__":
    unittest.main()
