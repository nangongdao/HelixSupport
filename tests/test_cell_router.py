"""Tests for cell routing and health management."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.cell_router import (
    CellRegistry,
    CellSpec,
    check_cell_health,
    forward_request_to_cell,
    route_request_to_cell,
)


class TestCellRegistry(unittest.TestCase):
    def setUp(self) -> None:
        self.cells = {
            "cell-default": CellSpec(
                cell_id="cell-default",
                db_url="postgresql://localhost:5432/db1",
                redis_url="redis://localhost:6379/0",
                health_url="http://localhost:8000/health",
                region="us-east-1",
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
        self.registry = CellRegistry(self.cells)

    def test_get_cell(self) -> None:
        cell = self.registry.get_cell("cell-default")
        self.assertIsNotNone(cell)
        self.assertEqual(cell.cell_id, "cell-default")
        self.assertEqual(cell.region, "us-east-1")

    def test_get_cell_not_found(self) -> None:
        cell = self.registry.get_cell("cell-nonexistent")
        self.assertIsNone(cell)

    def test_list_cells_all(self) -> None:
        cells = self.registry.list_cells()
        self.assertEqual(len(cells), 2)

    def test_list_cells_filtered_by_region(self) -> None:
        cells = self.registry.list_cells(region="us-east-1")
        self.assertEqual(len(cells), 1)
        self.assertEqual(cells[0].cell_id, "cell-default")

    def test_list_cells_healthy_only(self) -> None:
        self.registry.update_health("cell-default", is_healthy=True)
        self.registry.update_health("cell-premium", is_healthy=False, failure_reason="timeout")

        cells = self.registry.list_cells(healthy_only=True)
        self.assertEqual(len(cells), 1)
        self.assertEqual(cells[0].cell_id, "cell-default")

    def test_update_health(self) -> None:
        self.registry.update_health("cell-default", is_healthy=False, failure_reason="connect_error")
        health = self.registry.get_health("cell-default")
        self.assertIsNotNone(health)
        self.assertFalse(health.is_healthy)
        self.assertEqual(health.failure_reason, "connect_error")

    def test_get_health_unknown_cell(self) -> None:
        health = self.registry.get_health("cell-unknown")
        self.assertIsNone(health)


class TestRouteRequestToCell(unittest.TestCase):
    def setUp(self) -> None:
        self.cells = {
            "cell-default": CellSpec(
                cell_id="cell-default",
                db_url="postgresql://localhost:5432/db1",
                redis_url="redis://localhost:6379/0",
                health_url="http://localhost:8000/health",
                region="us-east-1",
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
        self.registry = CellRegistry(self.cells)

    def test_route_to_specified_cell(self) -> None:
        cell = route_request_to_cell(self.registry, tenant_cell="cell-premium")
        self.assertEqual(cell.cell_id, "cell-premium")

    def test_route_to_fallback_when_no_tenant_cell(self) -> None:
        cell = route_request_to_cell(self.registry, tenant_cell=None, fallback_cell="cell-default")
        self.assertEqual(cell.cell_id, "cell-default")

    def test_route_raises_when_cell_not_found(self) -> None:
        with self.assertRaises(LookupError) as ctx:
            route_request_to_cell(self.registry, tenant_cell="cell-nonexistent")
        self.assertIn("Cell not found", str(ctx.exception))

    def test_route_fallback_when_target_unhealthy(self) -> None:
        self.registry.update_health("cell-premium", is_healthy=False, failure_reason="timeout")
        cell = route_request_to_cell(
            self.registry,
            tenant_cell="cell-premium",
            fallback_cell="cell-default",
        )
        self.assertEqual(cell.cell_id, "cell-default")

    def test_route_raises_when_target_unhealthy_and_no_fallback(self) -> None:
        self.registry.update_health("cell-default", is_healthy=False, failure_reason="timeout")
        with self.assertRaises(LookupError) as ctx:
            route_request_to_cell(
                self.registry,
                tenant_cell="cell-default",
                fallback_cell="cell-default",
            )
        self.assertIn("Cell unhealthy and no fallback available", str(ctx.exception))


class TestCheckCellHealth(unittest.IsolatedAsyncioTestCase):
    async def test_healthy_cell(self) -> None:
        cell = CellSpec(
            cell_id="cell-test",
            db_url="postgresql://localhost:5432/db",
            redis_url="redis://localhost:6379/0",
            health_url="http://localhost:8000/health",
            region="us-east-1",
            capacity_tier="default",
        )

        mock_response = AsyncMock()
        mock_response.status_code = 200

        with patch("httpx.AsyncClient.get", return_value=mock_response):
            is_healthy, reason = await check_cell_health(cell)
            self.assertTrue(is_healthy)
            self.assertIsNone(reason)

    async def test_unhealthy_cell_http_error(self) -> None:
        cell = CellSpec(
            cell_id="cell-test",
            db_url="postgresql://localhost:5432/db",
            redis_url="redis://localhost:6379/0",
            health_url="http://localhost:8000/health",
            region="us-east-1",
            capacity_tier="default",
        )

        mock_response = AsyncMock()
        mock_response.status_code = 503

        with patch("httpx.AsyncClient.get", return_value=mock_response):
            is_healthy, reason = await check_cell_health(cell)
            self.assertFalse(is_healthy)
            self.assertEqual(reason, "HTTP 503")

    async def test_unhealthy_cell_timeout(self) -> None:
        cell = CellSpec(
            cell_id="cell-test",
            db_url="postgresql://localhost:5432/db",
            redis_url="redis://localhost:6379/0",
            health_url="http://localhost:8000/health",
            region="us-east-1",
            capacity_tier="default",
        )

        with patch("httpx.AsyncClient.get", side_effect=httpx.TimeoutException("timeout")):
            is_healthy, reason = await check_cell_health(cell)
            self.assertFalse(is_healthy)
            self.assertEqual(reason, "timeout")

    async def test_unhealthy_cell_connect_error(self) -> None:
        cell = CellSpec(
            cell_id="cell-test",
            db_url="postgresql://localhost:5432/db",
            redis_url="redis://localhost:6379/0",
            health_url="http://localhost:8000/health",
            region="us-east-1",
            capacity_tier="default",
        )

        with patch("httpx.AsyncClient.get", side_effect=httpx.ConnectError("connection refused")):
            is_healthy, reason = await check_cell_health(cell)
            self.assertFalse(is_healthy)
            self.assertIn("connect_error", reason)


class TestForwardRequestToCell(unittest.IsolatedAsyncioTestCase):
    async def test_forward_get_request(self) -> None:
        cell = CellSpec(
            cell_id="cell-test",
            db_url="postgresql://localhost:5432/db",
            redis_url="redis://localhost:6379/0",
            health_url="http://localhost:8000/health",
            region="us-east-1",
            capacity_tier="default",
        )

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "ok"}
        mock_response.aread = AsyncMock()
        mock_response.raise_for_status = lambda: None

        with patch("httpx.AsyncClient.request", return_value=mock_response):
            response = await forward_request_to_cell(
                cell,
                method="GET",
                path="/api/conversations",
            )
            self.assertEqual(response.status_code, 200)

    async def test_forward_post_request_with_body(self) -> None:
        cell = CellSpec(
            cell_id="cell-test",
            db_url="postgresql://localhost:5432/db",
            redis_url="redis://localhost:6379/0",
            health_url="http://localhost:8000/health",
            region="us-east-1",
            capacity_tier="default",
        )

        mock_response = AsyncMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": "conv-123"}
        mock_response.aread = AsyncMock()
        mock_response.raise_for_status = lambda: None

        with patch("httpx.AsyncClient.request", return_value=mock_response):
            response = await forward_request_to_cell(
                cell,
                method="POST",
                path="/api/conversations",
                json_body={"customer_name": "Test User"},
            )
            self.assertEqual(response.status_code, 201)


if __name__ == "__main__":
    unittest.main()
