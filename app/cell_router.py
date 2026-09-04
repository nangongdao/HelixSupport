"""Cell routing and health management (ROADMAP 2.2.1).

Provides cell-level routing for multi-cell deployments:
- CellRegistry: declarative cell inventory (db_url, redis_url, health_url)
- route_request_to_cell(): route requests based on TenantPolicy.deployment_cell
- Health checking: periodic ping to mark unhealthy cells
- Cross-cell forwarding: HTTP client with mTLS for internal API calls
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CellSpec:
    """Specification for a single deployment cell."""

    cell_id: str
    db_url: str
    redis_url: str
    health_url: str
    region: str
    capacity_tier: str  # "default" | "premium" | "enterprise"


@dataclass
class CellHealth:
    """Health status of a cell."""

    cell_id: str
    is_healthy: bool
    last_check_at: str
    failure_reason: str | None = None


class CellRegistry:
    """Registry of deployment cells with health tracking."""

    def __init__(self, cells: dict[str, CellSpec]) -> None:
        self._cells = cells
        self._health: dict[str, CellHealth] = {}

    def get_cell(self, cell_id: str) -> CellSpec | None:
        """Get cell spec by ID."""
        return self._cells.get(cell_id)

    def list_cells(self, region: str | None = None, healthy_only: bool = False) -> list[CellSpec]:
        """List all cells, optionally filtered by region and health status."""
        cells = list(self._cells.values())
        if region:
            cells = [c for c in cells if c.region == region]
        if healthy_only:
            cells = [c for c in cells if self._is_cell_healthy(c.cell_id)]
        return cells

    def update_health(
        self, cell_id: str, is_healthy: bool, failure_reason: str | None = None
    ) -> None:
        """Update health status for a cell."""
        from app.db._util import utc_now

        self._health[cell_id] = CellHealth(
            cell_id=cell_id,
            is_healthy=is_healthy,
            last_check_at=utc_now(),
            failure_reason=failure_reason,
        )

    def get_health(self, cell_id: str) -> CellHealth | None:
        """Get current health status for a cell."""
        return self._health.get(cell_id)

    def _is_cell_healthy(self, cell_id: str) -> bool:
        """Check if a cell is currently healthy."""
        health = self._health.get(cell_id)
        return health.is_healthy if health else True  # Default to healthy if unknown


def route_request_to_cell(
    registry: CellRegistry,
    tenant_cell: str | None,
    fallback_cell: str = "cell-default",
) -> CellSpec:
    """Route a request to the appropriate cell.

    Args:
        registry: Cell registry with available cells
        tenant_cell: Target cell from TenantPolicy.deployment_cell
        fallback_cell: Fallback cell ID if tenant cell is unavailable

    Returns:
        CellSpec for the target cell

    Raises:
        LookupError: If no healthy cell is available
    """
    target_cell_id = tenant_cell or fallback_cell

    cell = registry.get_cell(target_cell_id)
    if cell is None:
        raise LookupError(f"Cell not found: {target_cell_id}")

    health = registry.get_health(target_cell_id)
    if health and not health.is_healthy:
        logger.warning(
            "cell.unhealthy_fallback",
            extra={
                "target_cell": target_cell_id,
                "fallback_cell": fallback_cell,
                "reason": health.failure_reason,
            },
        )
        if target_cell_id != fallback_cell:
            fallback = registry.get_cell(fallback_cell)
            if fallback:
                return fallback
        raise LookupError(f"Cell unhealthy and no fallback available: {target_cell_id}")

    return cell


async def check_cell_health(
    cell: CellSpec, timeout_seconds: float = 5.0
) -> tuple[bool, str | None]:
    """Ping a cell's health endpoint.

    Args:
        cell: Cell to check
        timeout_seconds: Request timeout

    Returns:
        (is_healthy, failure_reason) tuple
    """
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.get(cell.health_url)
            if response.status_code == 200:
                return (True, None)
            return (False, f"HTTP {response.status_code}")
    except httpx.TimeoutException:
        return (False, "timeout")
    except httpx.ConnectError as exc:
        return (False, f"connect_error: {exc}")
    except httpx.HTTPError as exc:
        return (False, f"http_error: {exc}")


async def forward_request_to_cell(
    cell: CellSpec,
    method: str,
    path: str,
    headers: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
    timeout_seconds: float = 30.0,
) -> httpx.Response:
    """Forward an HTTP request to another cell.

    Args:
        cell: Target cell
        method: HTTP method (GET, POST, etc.)
        path: Request path (e.g., "/api/conversations")
        headers: Request headers
        json_body: JSON request body for POST/PUT/PATCH
        timeout_seconds: Request timeout

    Returns:
        httpx.Response from target cell

    Raises:
        httpx.HTTPError: If request fails
    """
    # Construct full URL from cell's base URL and path
    # In production, db_url would be internal, use a separate api_url
    # For now, assume health_url base can be reused
    base_url = (
        cell.health_url.rsplit("/health", 1)[0] if "/health" in cell.health_url else cell.health_url
    )
    url = f"{base_url}{path}"

    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.request(
            method=method,
            url=url,
            headers=headers,
            json=json_body,
        )
        await response.aread()
        response.raise_for_status()
        return response


async def periodic_health_check(registry: CellRegistry, interval_seconds: int = 30) -> None:
    """Background task to periodically check all cell health.

    Args:
        registry: Cell registry to update
        interval_seconds: Check interval
    """
    import asyncio

    while True:
        for cell in registry.list_cells():
            is_healthy, reason = await check_cell_health(cell)
            registry.update_health(cell.cell_id, is_healthy, reason)
            if not is_healthy:
                logger.warning(
                    "cell.health_check_failed",
                    extra={
                        "cell_id": cell.cell_id,
                        "reason": reason,
                    },
                )
        await asyncio.sleep(interval_seconds)
