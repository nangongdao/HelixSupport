"""Tests for regional failover (ROADMAP 2.2.3)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from app.cell_router import CellRegistry, CellSpec
from app.control_plane import DataPlaneConfig, TenantControlPlane, TenantPolicy
from app.region_failover import (
    FailoverError,
    FailoverState,
    check_region_health,
    find_healthy_cell_in_region,
    initiate_failover,
)
from app.residency import REGION_INVENTORY, RegionSpec

SECRET = "test-control-secret-32bytes-long-0001"

REGION_INVENTORY_MULTI = {
    **REGION_INVENTORY,
    "us-west-2": RegionSpec(
        name="us-west-2",
        storage_location="us-west-2-node",
        backup_target="us-west-2-backup",
    ),
}


def _build_registry() -> CellRegistry:
    cells = {
        "cell-default": CellSpec(
            cell_id="cell-default",
            db_url="postgresql://localhost:5432/db1",
            redis_url="redis://localhost:6379/0",
            health_url="http://localhost:8000/health",
            region="local",
            capacity_tier="default",
        ),
        "cell-premium": CellSpec(
            cell_id="cell-premium",
            db_url="postgresql://localhost:5433/db2",
            redis_url="redis://localhost:6380/0",
            health_url="http://localhost:8001/health",
            region="us-west-2",
            capacity_tier="premium",
        ),
    }
    return CellRegistry(cells)


def _build_control_plane(tmp: str) -> tuple[TenantControlPlane, DataPlaneConfig]:
    from app.database import Database

    db = Database(Path(tmp) / "failover.db")
    db.initialize()
    db.ensure_tenant("tenant-1")
    control = TenantControlPlane(db, SECRET)
    data = DataPlaneConfig(db, SECRET)
    # Keep the db on the control plane so tests can close it before cleanup.
    control._db = db  # type: ignore[attr-defined]
    return control, data


class TestCheckRegionHealth(unittest.IsolatedAsyncioTestCase):
    async def _cell(self) -> CellSpec:
        cell = _build_registry().get_cell("cell-default")
        assert cell is not None
        return cell

    async def test_healthy_cell(self) -> None:
        cell = await self._cell()
        mock_response = AsyncMock()
        mock_response.status_code = 200

        with patch("app.region_failover.check_cell_health", return_value=(True, None)):
            is_healthy, reason = await check_region_health(cell)
        self.assertTrue(is_healthy)
        self.assertIsNone(reason)

    async def test_unhealthy_cell(self) -> None:
        cell = await self._cell()
        with patch("app.region_failover.check_cell_health", return_value=(False, "timeout")):
            is_healthy, reason = await check_region_health(cell)
        self.assertFalse(is_healthy)
        self.assertEqual(reason, "timeout")

    async def test_probe_never_raises(self) -> None:
        cell = await self._cell()
        # The thin proxy must catch every failure from the underlying probe.
        with patch(
            "app.region_failover.check_cell_health",
            side_effect=httpx.ConnectError("connection refused"),
        ):
            is_healthy, reason = await check_region_health(cell)
        self.assertFalse(is_healthy)
        self.assertIn("connection refused", reason or "")


class TestFindHealthyCellInRegion(unittest.IsolatedAsyncioTestCase):
    async def test_finds_healthy_cell(self) -> None:
        registry = _build_registry()

        async def _probe(cell, timeout_seconds=5.0):
            return (cell.cell_id == "cell-premium", None)

        with patch("app.region_failover.check_cell_health", side_effect=_probe):
            cell = await find_healthy_cell_in_region(registry, "us-west-2")
        assert cell is not None
        self.assertEqual(cell.cell_id, "cell-premium")

    async def test_none_when_no_healthy_cell(self) -> None:
        registry = _build_registry()
        with patch("app.region_failover.check_cell_health", return_value=(False, "timeout")):
            cell = await find_healthy_cell_in_region(registry, "us-west-2")
        self.assertIsNone(cell)


class TestInitiateFailover(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.control, self.data = _build_control_plane(self._tmp.name)
        self.registry = _build_registry()
        # Seed an initial policy on cell-default so effective_policy works.
        self.control.set_policy(
            "tenant-1",
            TenantPolicy(plan="standard", region="local", deployment_cell="cell-default"),
        )

    def tearDown(self) -> None:
        db = getattr(self.control, "_db", None)
        if db is not None:
            db.close()
        self._tmp.cleanup()

    async def test_failover_success(self) -> None:
        with patch("app.region_failover.check_cell_health", return_value=(True, None)):
            state = await initiate_failover(
                self.control,
                self.data,
                self.registry,
                "tenant-1",
                "cell-premium",
                region_inventory=REGION_INVENTORY_MULTI,
            )

        self.assertEqual(state.status, "committed")
        self.assertEqual(state.to_cell, "cell-premium")
        self.assertEqual(state.to_region, "us-west-2")
        self.assertIsNotNone(state.policy_version)
        self.assertGreaterEqual(len(state.steps), 3)

        policy = self.data.effective_policy("tenant-1")
        self.assertEqual(policy.deployment_cell, "cell-premium")
        self.assertEqual(policy.region, "us-west-2")

    async def test_failover_rejects_unknown_cell(self) -> None:
        with self.assertRaises(FailoverError) as ctx:
            await initiate_failover(
                self.control,
                self.data,
                self.registry,
                "tenant-1",
                "cell-nonexistent",
                region_inventory=REGION_INVENTORY_MULTI,
            )
        self.assertIn("target cell not found", str(ctx.exception))

    async def test_failover_rejects_unhealthy_target(self) -> None:
        with patch("app.region_failover.check_cell_health", return_value=(False, "timeout")):
            with self.assertRaises(FailoverError) as ctx:
                await initiate_failover(
                    self.control,
                    self.data,
                    self.registry,
                    "tenant-1",
                    "cell-premium",
                    region_inventory=REGION_INVENTORY_MULTI,
                )
        self.assertIn("unhealthy", str(ctx.exception))

    async def test_failover_can_skip_health_check(self) -> None:
        with patch("app.region_failover.check_cell_health", return_value=(False, "timeout")):
            state = await initiate_failover(
                self.control,
                self.data,
                self.registry,
                "tenant-1",
                "cell-premium",
                require_target_healthy=False,
                region_inventory=REGION_INVENTORY_MULTI,
            )
        self.assertEqual(state.status, "committed")

    async def test_failover_rejects_unknown_region(self) -> None:
        # Default inventory only knows "local"; us-west-2 is not approved.
        with patch("app.region_failover.check_cell_health", return_value=(True, None)):
            with self.assertRaises(FailoverError) as ctx:
                await initiate_failover(
                    self.control,
                    self.data,
                    self.registry,
                    "tenant-1",
                    "cell-premium",
                )
        self.assertIn("not in the region inventory", str(ctx.exception))

    async def test_failover_rejects_same_cell(self) -> None:
        with patch("app.region_failover.check_cell_health", return_value=(True, None)):
            with self.assertRaises(FailoverError) as ctx:
                await initiate_failover(
                    self.control,
                    self.data,
                    self.registry,
                    "tenant-1",
                    "cell-default",
                    region_inventory=REGION_INVENTORY_MULTI,
                )
        self.assertIn("already on cell", str(ctx.exception))


class TestFailoverStateTransitions(unittest.TestCase):
    def test_planned_to_committed(self) -> None:
        state = FailoverState.planned(
            tenant_id="t1",
            from_cell="a",
            to_cell="b",
            from_region="local",
            to_region="us-west-2",
        )
        self.assertEqual(state.status, "planned")
        committed = state.committed(policy_version=3, steps=("x", "y"))
        self.assertEqual(committed.status, "committed")
        self.assertEqual(committed.policy_version, 3)
        self.assertTrue(committed.completed_at)

    def test_planned_to_failed(self) -> None:
        state = FailoverState.planned(
            tenant_id="t1",
            from_cell="a",
            to_cell="b",
            from_region="local",
            to_region="us-west-2",
        )
        failed = state.failed("target unhealthy")
        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.reason, "target unhealthy")
        self.assertTrue(failed.completed_at)


if __name__ == "__main__":
    unittest.main()
