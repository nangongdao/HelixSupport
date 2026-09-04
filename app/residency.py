"""Tenant data-residency policy (ROADMAP 43.4).

Single source of truth for "where may this tenant's data live".  A tenant's
residency is fixed at creation time in ``tenants.region`` (migration v40)
and mirrored into the signed control-plane policy; this module maps a
region name to the storage, backup, and cross-border posture every other
subsystem must honour:

- backups: ``scripts/backup.py`` stamps each manifest with the residency
  summary of the tenants it covers;
- evidence: ``scripts/generate_residency_pack.py`` renders the per-tenant
  pack and cross-border processing register from these tables.

The inventory is deliberately declarative data, not logic — deployments
override it wholesale via :data:`REGION_INVENTORY` replacement or by
passing their own mapping, keeping the module free of environment reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DEFAULT_REGION = "local"

# Data classifications from the v32 field registry, ordered most-restrictive
# last so ``max()`` picks the ceiling class of a region's allowed set.
DATA_CLASSES = ("public", "internal", "confidential", "restricted")


@dataclass(frozen=True)
class RegionSpec:
    """One deployment region and its residency posture."""

    name: str
    storage_location: str
    backup_target: str
    # Highest classification this region may hold; anything above is a
    # residency violation if found on a tenant pinned here.
    max_data_class: str = "restricted"
    # Regions whose operators may access this data for support (break-glass
    # is recorded per-access in audit; this is the standing allow-list).
    support_access_from: tuple[str, ...] = ()
    # Standing cross-border transfers this region participates in. Empty
    # means the region is closed — no replication or support export leaves it.
    cross_border_transfers: tuple[str, ...] = ()

    def allows_data_class(self, classification: str) -> bool:
        return (
            DATA_CLASSES.index(classification) <= DATA_CLASSES.index(self.max_data_class)
            if classification in DATA_CLASSES
            else False
        )


#: Default single-region inventory: everything stays local, no outbound
#: transfers. Deployments with real multi-region topology replace entries.
REGION_INVENTORY: dict[str, RegionSpec] = {
    DEFAULT_REGION: RegionSpec(
        name=DEFAULT_REGION,
        storage_location="primary-node",
        backup_target="primary-node",
    ),
}


class ResidencyError(ValueError):
    """A requested operation would move data outside its pinned region."""


def resolve_region(name: str | None) -> str:
    """Normalise a region name, defaulting unknown/absent to ``local``."""
    return (name or DEFAULT_REGION).strip().lower() or DEFAULT_REGION


def get_region_spec(name: str | None, inventory: dict[str, RegionSpec] | None = None) -> RegionSpec:
    """Look up a region's spec.

    Unknown names fall back to the ``local`` spec; callers that must not
    silently widen residency should check :func:`is_known_region` first —
    evidence packs and summaries flag unknown regions as
    ``"spec_status": "unknown-fallback-local"`` so a mistyped or unmanaged
    region can never masquerade as a reviewed posture.
    """
    table = REGION_INVENTORY if inventory is None else inventory
    resolved = resolve_region(name)
    return table.get(resolved, table[DEFAULT_REGION])


def is_known_region(name: str | None, inventory: dict[str, RegionSpec] | None = None) -> bool:
    """True when the (normalised) region exists in the inventory."""
    table = REGION_INVENTORY if inventory is None else inventory
    return resolve_region(name) in table


def summarize_tenant_residency(
    rows: list[dict[str, Any]], inventory: dict[str, RegionSpec] | None = None
) -> dict[str, Any]:
    """Summarise the residency spread of tenant rows for a backup manifest.

    ``rows`` are ``{"tenant_id", "region"}`` dicts as read from ``tenants``.
    The result records which regions the backup covers so a restore into
    the wrong region can be detected (see ``check_backup_residency``).
    """
    by_region: dict[str, list[str]] = {}
    for row in rows:
        region = resolve_region(row.get("region"))
        by_region.setdefault(region, []).append(str(row["tenant_id"]))
    table = REGION_INVENTORY if inventory is None else inventory
    return {
        "regions": {
            region: {
                "tenant_count": len(tenant_ids),
                "storage_location": get_region_spec(region, inventory).storage_location,
                "backup_target": get_region_spec(region, inventory).backup_target,
                "tenant_ids": sorted(tenant_ids),
                # Unknown regions fall back to the local spec; flag them so
                # an unmanaged name can't silently inherit its posture.
                "spec_status": ("known" if region in table else "unknown-fallback-local"),
            }
            for region, tenant_ids in sorted(by_region.items())
        },
        "single_write_region": len(by_region) <= 1,
        "cross_border_transfers": sorted(
            {
                transfer
                for region in by_region
                for transfer in get_region_spec(region, inventory).cross_border_transfers
            }
        ),
    }


def check_restore_compatibility(
    manifest_residency: dict[str, Any],
    target_regions: set[str],
) -> list[str]:
    """Regions a backup covers that the restore target does not host.

    Returns the violating region names; empty means the restore keeps every
    covered tenant inside an approved target region. Controlled failover is
    the operator adding the disaster-recovery region to ``target_regions``
    explicitly — the check never silently widens where data may live.
    """
    covered = set((manifest_residency or {}).get("regions") or {})
    approved = {resolve_region(r) for r in target_regions}
    return sorted(region for region in covered if resolve_region(region) not in approved)
