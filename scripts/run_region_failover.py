"""Controlled regional failover runbook (ROADMAP 2.2.3).

Usage:
    python scripts/run_region_failover.py --tenant <id> --target-cell <cell-id>
    python scripts/run_region_failover.py --tenant <id> --target-region <region>

Resolves the cell registry from the environment (CELL_REGISTRY_JSON or the
``cell_registry_config`` block), verifies the target is healthy, enforces
residency, and publishes a new signed control-plane snapshot. Prints the
machine-readable FailoverState on success.

Read-only flags:
    --dry-run       run every validation step but do not publish the policy.
    --skip-health   do not require the target cell to be healthy.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Allow running from a source checkout without installing.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.cell_router import CellRegistry, CellSpec
from app.config import Settings


def _load_cells(settings: Settings) -> dict[str, CellSpec]:
    config = settings.cell_registry_config
    if not config:
        raise SystemExit(
            "CELL_REGISTRY_JSON (or the cell_registry_config block) must be set "
            "to resolve target cells"
        )
    return {
        cell_id: CellSpec(
            cell_id=cell_id,
            db_url=spec["db_url"],
            redis_url=spec["redis_url"],
            health_url=spec["health_url"],
            region=spec["region"],
            capacity_tier=spec.get("capacity_tier", "default"),
        )
        for cell_id, spec in config.items()
    }


async def _main(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    registry = CellRegistry(_load_cells(settings))

    from app.control_plane import DataPlaneConfig, TenantControlPlane
    from app.database import Database
    from app.region_failover import (
        FailoverError,
        find_healthy_cell_in_region,
        initiate_failover,
    )

    db = Database(settings.database_path)
    db.initialize()
    secret = settings.control_plane_secret or settings.widget_secret
    if len(secret.encode("utf-8")) < 32:
        print("ERROR: CONTROL_PLANE_SECRET must be set (>= 32 bytes)", file=sys.stderr)
        return 2
    control = TenantControlPlane(db, secret)
    data = DataPlaneConfig(db, secret)

    target_cell = args.target_cell
    if not target_cell and args.target_region:
        cell = await find_healthy_cell_in_region(registry, args.target_region)
        if cell is None:
            print(
                f"ERROR: no healthy cell found in region {args.target_region!r}",
                file=sys.stderr,
            )
            return 2
        target_cell = cell.cell_id
    if not target_cell:
        print("ERROR: --target-cell (or --target-region) is required", file=sys.stderr)
        return 2

    print(f"=== Failover runbook: tenant={args.tenant} target={target_cell}")
    if args.dry_run:
        print("=== DRY RUN (no policy published)")
    # Every region present in the cell registry is deployment-approved;
    # pass them as the residency inventory so the failover does not treat
    # them as unknown regions.
    from app.residency import RegionSpec

    region_inventory = {
        cell.region: RegionSpec(
            name=cell.region,
            storage_location=f"{cell.region}-node",
            backup_target=f"{cell.region}-backup",
        )
        for cell in registry.list_cells()
    }
    try:
        state = await initiate_failover(
            control,
            data,
            registry,
            args.tenant,
            target_cell,
            require_target_healthy=not args.skip_health,
            region_inventory=region_inventory,
            dry_run=args.dry_run,
        )
    except FailoverError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(state.__dict__, ensure_ascii=False, indent=2))
    if args.dry_run:
        print("=== DRY RUN complete (policy NOT published)")
    else:
        print("=== failover committed; verify traffic before declaring complete")
    db.close()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Controlled tenant regional failover")
    parser.add_argument("--tenant", required=True, help="tenant id to fail over")
    parser.add_argument("--target-cell", default="", help="target cell id")
    parser.add_argument("--target-region", default="", help="target region name")
    parser.add_argument("--dry-run", action="store_true", help="validate only, do not publish")
    parser.add_argument("--skip-health", action="store_true", help="skip target health check")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main(args)))
