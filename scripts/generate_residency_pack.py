"""Generate per-tenant residency evidence packs (ROADMAP 43.4).

For every provisioned tenant, renders a JSON evidence document combining:

- the residency pinned at creation time (``tenants.region``, migration v40);
- the signed control-plane policy snapshot covering that tenant (region,
  cell, plan, version, signature — re-verified before inclusion);
- the data-field registry classifications the tenant's region may hold;
- the cross-border processing register derived from the region inventory;
- the backup manifests whose residency summary covers this tenant.

Usage:
    python scripts/generate_residency_pack.py --database data/support.db \
        --output residency-packs/ [--secret-env CONTROL_PLANE_SECRET]

Output: one ``<tenant>.residency.json`` per tenant plus
``_cross_border_register.json`` aggregating the register across tenants.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
import sys
from pathlib import Path

from app.control_plane import ControlPlaneError, TenantControlPlane
from app.residency import (
    DATA_CLASSES,
    REGION_INVENTORY,
    get_region_spec,
    is_known_region,
    resolve_region,
)

logger = logging.getLogger(__name__)

SNAPSHOT_SCHEMA = "helix.residency.evidence/v1"


def _read_tenant_rows(database_path: Path) -> list[dict]:
    connection = sqlite3.connect(str(database_path))
    connection.row_factory = sqlite3.Row
    try:
        try:
            rows = connection.execute(
                "SELECT id, name, created_at, region FROM tenants ORDER BY id"
            ).fetchall()
        except sqlite3.OperationalError:
            rows = connection.execute(
                "SELECT id, name, created_at FROM tenants ORDER BY id"
            ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def _read_data_field_registry(database_path: Path) -> dict[str, dict]:
    """Field → classification from the v32 registry (empty if absent)."""
    connection = sqlite3.connect(str(database_path))
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT field, classification, region FROM data_field_registry"
        ).fetchall()
        return {str(row["field"]): dict(row) for row in rows}
    except sqlite3.OperationalError:
        return {}
    finally:
        connection.close()


def build_tenant_pack(
    tenant: dict,
    *,
    control_plane: TenantControlPlane | None,
    signing_secret: str | None,
    field_registry: dict[str, dict],
    backup_manifests: list[dict],
) -> dict:
    """Assemble one tenant's residency evidence document.

    Fails closed on policy/region disagreement when a signed snapshot is
    available: the pack records the mismatch instead of certifying it.
    """
    tenant_id = str(tenant["id"])
    pinned_region = resolve_region(tenant.get("region"))
    spec = get_region_spec(pinned_region)

    snapshot_evidence: dict = {"status": "absent"}
    mismatches: list[str] = []
    if control_plane is not None:
        try:
            snapshot = control_plane.issue_snapshot(tenant_id)
            snapshot_evidence = {
                "status": "verified",
                "version": snapshot.version,
                "issued_at": snapshot.issued_at,
                "policy": snapshot.policy.canonical(),
                "signature": snapshot.signature,
            }
            if resolve_region(snapshot.policy.region) != pinned_region:
                mismatches.append(
                    f"control-plane policy region {snapshot.policy.region!r} != "
                    f"pinned region {pinned_region!r}"
                )
        except LookupError:
            mismatches.append("no control-plane policy exists for this tenant")
        except (ValueError, ControlPlaneError) as exc:
            # Tampered/expired/structurally invalid snapshots are recorded,
            # never certified — and must not abort the remaining packs.
            snapshot_evidence = {"status": "invalid", "detail": str(exc)}
            mismatches.append(f"snapshot verification failed: {exc}")

    fields_out_of_region = sorted(
        field
        for field, meta in field_registry.items()
        if not spec.allows_data_class(str(meta["classification"]))
    )

    covering_backups = [
        manifest.get("backup_file")
        for manifest in backup_manifests
        if tenant_id
        in (manifest.get("residency", {}).get("regions", {}) or {})
        .get(pinned_region, {})
        .get("tenant_ids", [])
    ]

    return {
        "schema": SNAPSHOT_SCHEMA,
        "tenant_id": tenant_id,
        "tenant_name": tenant["name"],
        "created_at": tenant["created_at"],
        # Top-level consistency verdict: False whenever any mismatch was
        # recorded, so consumers never read "verified" as "fully compliant".
        "consistent": not mismatches,
        "residency": {
            "region": pinned_region,
            "spec_status": (
                "known" if is_known_region(pinned_region) else "unknown-fallback-local"
            ),
            "storage_location": spec.storage_location,
            "backup_target": spec.backup_target,
            "max_data_class": spec.max_data_class,
            "support_access_from": list(spec.support_access_from),
        },
        "control_plane_snapshot": snapshot_evidence,
        "data_fields": {
            "registry_size": len(field_registry),
            "fields_exceeding_region_class": fields_out_of_region,
        },
        "backups_covering_tenant": [name for name in covering_backups if name],
        "cross_border_transfers": list(spec.cross_border_transfers),
        "mismatches": mismatches,
    }


def generate_packs(
    database_path: Path,
    output_dir: Path,
    *,
    signing_secret: str | None = None,
    backups_dir: Path | None = None,
) -> dict:
    """Write one pack per tenant plus the aggregated cross-border register."""
    tenants = _read_tenant_rows(database_path)
    field_registry = _read_data_field_registry(database_path)
    control_plane: TenantControlPlane | None = None
    if signing_secret:
        control_plane = TenantControlPlane(_DatabaseShim(database_path), signing_secret)

    backup_manifests = _load_backup_manifests(backups_dir) if backups_dir else []

    output_dir.mkdir(parents=True, exist_ok=True)
    # Tenant ids come from the database, not the API's pattern-validated
    # boundary; sanitise before they become filenames so a tampered or
    # legacy id cannot write outside the output directory.
    safe_id_pattern = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
    register_entries: list[dict] = []
    for tenant in tenants:
        pack = build_tenant_pack(
            tenant,
            control_plane=control_plane,
            signing_secret=signing_secret,
            field_registry=field_registry,
            backup_manifests=backup_manifests,
        )
        tenant_id = str(tenant["id"])
        if not safe_id_pattern.match(tenant_id):
            logger.warning("Skipping pack for unsafe tenant id %r", tenant_id)
            continue
        pack_path = output_dir / f"{tenant_id}.residency.json"
        pack_path.write_text(json.dumps(pack, indent=2), encoding="utf-8")
        register_entries.append(
            {
                "tenant_id": pack["tenant_id"],
                "region": pack["residency"]["region"],
                "cross_border_transfers": pack["cross_border_transfers"],
                "mismatches": pack["mismatches"],
            }
        )
        logger.info("Wrote %s", pack_path)

    # Cross-border processing register: only transfers that actually leave
    # the region appear; closed regions contribute nothing.
    register = {
        "schema": SNAPSHOT_SCHEMA,
        "generated_from": str(database_path),
        "data_classes": list(DATA_CLASSES),
        "regions_in_inventory": sorted(REGION_INVENTORY),
        "entries": [entry for entry in register_entries if entry["cross_border_transfers"]],
    }
    register_path = output_dir / "_cross_border_register.json"
    register_path.write_text(json.dumps(register, indent=2), encoding="utf-8")
    logger.info("Wrote %s (%d entries)", register_path, len(register["entries"]))
    return {"packs": len(register_entries), "register": str(register_path)}


def _load_backup_manifests(backups_dir: Path) -> list[dict]:
    manifests: list[dict] = []
    for path in sorted(backups_dir.glob("*.manifest.json")):
        try:
            manifests.append(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            logger.warning("Skipping unreadable manifest %s", path)
    return manifests


class _DatabaseShim:
    """Minimal connect() surface so the pack can reuse TenantControlPlane."""

    def __init__(self, database_path: Path) -> None:
        self._path = database_path

    def connect(self) -> "_ConnectionScope":
        connection = sqlite3.connect(str(self._path))
        connection.row_factory = sqlite3.Row
        return _ConnectionScope(connection)


class _ConnectionScope:
    """sqlite3 connection wrapper honouring the ``with`` protocol."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def __enter__(self) -> sqlite3.Connection:
        return self._connection

    def __exit__(self, *exc: object) -> None:
        self._connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, help="Path to support.db")
    parser.add_argument("--output", required=True, help="Directory for evidence packs")
    parser.add_argument(
        "--secret-env",
        default="CONTROL_PLANE_SECRET",
        help="Environment variable holding the control-plane signing secret "
        "(enables snapshot verification; omit env to skip)",
    )
    parser.add_argument(
        "--backups-dir",
        help="Directory of backup manifests to correlate into each pack",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    secret = os.environ.get(args.secret_env) or None
    try:
        result = generate_packs(
            Path(args.database),
            Path(args.output),
            signing_secret=secret,
            backups_dir=Path(args.backups_dir) if args.backups_dir else None,
        )
    except ValueError as exc:
        # Most commonly a too-short CONTROL_PLANE_SECRET rejected by
        # TenantControlPlane — report it clearly instead of a traceback.
        logger.error("%s", exc)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
