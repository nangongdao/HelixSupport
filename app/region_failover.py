"""Regional failover (ROADMAP 2.2.3).

Controlled, audit-trailed failover of a tenant (or a whole cell) from one
region to another:

- ``check_region_health``  — probe a cell's health endpoint (reuses the cell
  router's probe, never blocks the primary path).
- ``initiate_failover``   — switch a tenant's signed control-plane policy to
  the target cell/region (plan/region/deployment_cell are high-risk fields;
  publishing a new signed snapshot is the only accepted switch mechanism).
- ``FailoverState``       — machine-readable record of the switch so runbooks
  and dashboards can show planned → committed progression.

Residency is honoured: the target region must exist in the region inventory,
otherwise the failover refuses to widen where the tenant's data may live.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.cell_router import CellRegistry, CellSpec, check_cell_health
from app.control_plane import DataPlaneConfig, TenantControlPlane, TenantPolicy
from app.db._util import utc_now
from app.residency import is_known_region, resolve_region

logger = logging.getLogger(__name__)

#: Lifecycle of a failover run. ``planned`` means the operator has requested
#: it but no policy change is published yet; ``committed`` means the new
#: signed snapshot is live; ``failed`` means a step rejected the switch.
FAILOVER_STATUSES = ("planned", "committed", "failed")


class FailoverError(RuntimeError):
    """A failover step was rejected (target unknown/unhealthy/residency)."""


@dataclass(frozen=True)
class FailoverState:
    """One failover run's record."""

    tenant_id: str
    from_cell: str
    to_cell: str
    from_region: str
    to_region: str
    status: str = "planned"
    reason: str = ""
    started_at: str = ""
    completed_at: str = ""
    policy_version: int | None = None
    steps: tuple[str, ...] = ()

    @classmethod
    def planned(
        cls,
        tenant_id: str,
        from_cell: str,
        to_cell: str,
        from_region: str,
        to_region: str,
    ) -> FailoverState:
        return cls(
            tenant_id=tenant_id,
            from_cell=from_cell,
            to_cell=to_cell,
            from_region=from_region,
            to_region=to_region,
            status="planned",
            started_at=utc_now(),
        )

    def committed(self, policy_version: int, steps: tuple[str, ...]) -> FailoverState:
        return FailoverState(
            tenant_id=self.tenant_id,
            from_cell=self.from_cell,
            to_cell=self.to_cell,
            from_region=self.from_region,
            to_region=self.to_region,
            status="committed",
            reason=self.reason,
            started_at=self.started_at,
            completed_at=utc_now(),
            policy_version=policy_version,
            steps=steps,
        )

    def failed(self, reason: str) -> FailoverState:
        return FailoverState(
            tenant_id=self.tenant_id,
            from_cell=self.from_cell,
            to_cell=self.to_cell,
            from_region=self.from_region,
            to_region=self.to_region,
            status="failed",
            reason=reason,
            started_at=self.started_at,
            completed_at=utc_now(),
            steps=self.steps,
        )


async def check_region_health(
    cell: CellSpec, timeout_seconds: float = 5.0
) -> tuple[bool, str | None]:
    """Probe one cell's health endpoint (fail-safe: never raises).

    Returns ``(is_healthy, failure_reason)``; a non-200 status or any HTTP
    error is treated as unhealthy. The probe is a plain GET to the cell's
    ``health_url`` and never carries tenant data.
    """
    try:
        return await check_cell_health(cell, timeout_seconds=timeout_seconds)
    except Exception as exc:  # fail-safe: a probe must never break failover
        return (False, f"probe_error: {exc}")


async def find_healthy_cell_in_region(
    registry: CellRegistry, region: str, *, timeout_seconds: float = 5.0
) -> CellSpec | None:
    """Return the first healthy cell in ``region``, or ``None``.

    Used by runbooks to pick a target when the operator names a region
    instead of a specific cell.
    """
    for cell in registry.list_cells(region=region):
        is_healthy, _ = await check_region_health(cell, timeout_seconds=timeout_seconds)
        if is_healthy:
            return cell
    return None


async def initiate_failover(
    control_plane: TenantControlPlane,
    data_plane: DataPlaneConfig,
    registry: CellRegistry,
    tenant_id: str,
    target_cell_id: str,
    *,
    require_target_healthy: bool = True,
    region_inventory: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> FailoverState:
    """Switch ``tenant_id``'s policy to ``target_cell_id`` (ROADMAP 2.2.3).

    Steps:
    1. Resolve the current policy from the data plane (last-known-good).
    2. Validate the target cell exists and (by default) is healthy.
    3. Enforce residency: the target region must be a known region.
    4. Publish a new signed snapshot (plan/region/deployment_cell changed).
    5. Return a committed :class:`FailoverState`.

    When ``dry_run=True``, every validation step (target lookup, health,
    residency) runs but no policy is published and the returned state stays
    ``planned`` — used by runbooks to preview a switch safely.

    Raises:
        FailoverError: target cell unknown/unhealthy, residency violation, or
            the control plane rejected the new policy.
    """
    try:
        current = data_plane.effective_policy(tenant_id)
    except Exception as exc:
        raise FailoverError(
            f"cannot read current policy for tenant {tenant_id}: {exc} "
            "(create an initial policy via the control-plane API first)"
        ) from exc

    target = registry.get_cell(target_cell_id)
    if target is None:
        raise FailoverError(f"target cell not found: {target_cell_id}")

    if require_target_healthy:
        is_healthy, reason = await check_region_health(target)
        if not is_healthy:
            raise FailoverError(
                f"target cell {target_cell_id} is unhealthy: {reason or 'unknown'}"
            )

    to_region = resolve_region(target.region)
    if not is_known_region(to_region, region_inventory):
        raise FailoverError(
            f"target region {to_region!r} is not in the region inventory; "
            "refusing to widen residency"
        )

    from_cell = current.deployment_cell
    from_region = current.region
    if target_cell_id == from_cell:
        raise FailoverError(f"tenant {tenant_id} is already on cell {target_cell_id}")

    state = FailoverState.planned(
        tenant_id=tenant_id,
        from_cell=from_cell,
        to_cell=target_cell_id,
        from_region=from_region,
        to_region=to_region,
    )
    steps: list[str] = [
        f"target-cell-validated:{target_cell_id}",
        f"residency-ok:{to_region}",
    ]

    new_policy = TenantPolicy(
        plan=current.plan,
        region=to_region,
        deployment_cell=target_cell_id,
        feature_policy=current.feature_policy,
        model_policy=current.model_policy,
        credential_reference=current.credential_reference,
    )
    if dry_run:
        steps.append("dry-run:policy-not-published")
        logger.info(
            "region.failover_dry_run",
            extra={
                "tenant_id": tenant_id,
                "to_cell": target_cell_id,
                "to_region": to_region,
            },
        )
        return FailoverState(
            tenant_id=tenant_id,
            from_cell=from_cell,
            to_cell=target_cell_id,
            from_region=from_region,
            to_region=to_region,
            status="planned",
            reason="dry-run: policy not published",
            started_at=state.started_at,
            steps=tuple(steps),
        )
    try:
        snapshot = control_plane.set_policy(tenant_id, new_policy)
    except Exception as exc:
        raise FailoverError(
            f"control plane refused failover for tenant {tenant_id}: {exc}"
        ) from exc
    try:
        data_plane.apply_snapshot(snapshot)
    except Exception as exc:
        raise FailoverError(
            f"data plane rejected failover snapshot for tenant {tenant_id}: {exc}"
        ) from exc

    steps.append(f"policy-published:v{snapshot.version}")
    logger.warning(
        "region.failover_committed",
        extra={
            "tenant_id": tenant_id,
            "from_cell": from_cell,
            "to_cell": target_cell_id,
            "from_region": from_region,
            "to_region": to_region,
            "policy_version": snapshot.version,
        },
    )
    return state.committed(policy_version=snapshot.version, steps=tuple(steps))
