"""Cost dashboard API (ROADMAP 2.3.4).

Admin-facing endpoints exposing inference cost attribution: daily totals,
breakdowns by provider/model/date, and the anomaly status of the current
day against the tenant's recent baseline. All routes require ``admin:manage``
— cost data is tenant-wide spend.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.cost_attribution import CostAttributionService
from app.main import require_permission
from app.routers.common import RouteDeps
from app.security import Principal


def build_router(deps: RouteDeps) -> APIRouter:
    router = APIRouter(prefix="/api/analytics", tags=["analytics"])
    costs: CostAttributionService = deps.services.cost_attribution

    @router.get(
        "/costs/daily",
        summary="Summarize tenant inference cost over a date range",
        description=(
            "Aggregate the tenant's inference cost (turn count, prompt/completion "
            "tokens, USD cost) from the daily rollup. ``start_date``/``end_date`` "
            "are inclusive ISO dates; omitted ends default to the full history. "
            "Requires ``admin:manage``."
        ),
    )
    def daily_cost(
        principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
        start_date: Annotated[str | None, Query(alias="start_date")] = None,
        end_date: Annotated[str | None, Query(alias="end_date")] = None,
    ) -> dict:
        """Summarize the tenant's inference cost over an optional date range."""
        return costs.get_tenant_cost_summary(principal.tenant_id, since=start_date, until=end_date)

    @router.get(
        "/costs/by_agent",
        summary="Break down inference cost by agent",
        description=(
            "Per-agent spend (triage, language_detect, language_translate, "
            "copilot_suggest, copilot_rewrite, summary) for one date, from the "
            "per-inference detail table. Requires ``admin:manage``."
        ),
    )
    def cost_by_agent(
        principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
        date: Annotated[str | None, Query()] = None,
    ) -> list[dict]:
        """Break down inference cost by agent (triage/language/copilot/summary)."""
        return costs.get_cost_by_dimension(principal.tenant_id, "agent", date_str=date)

    @router.get(
        "/costs/by_prompt",
        summary="Break down inference cost by prompt version",
        description=(
            "Spend attributed to each prompt version (from the prompt registry) "
            "for one date. Rows without a version fall under ``unknown``. "
            "Requires ``admin:manage``."
        ),
    )
    def cost_by_prompt(
        principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
        date: Annotated[str | None, Query()] = None,
    ) -> list[dict]:
        """Break down inference cost by prompt version."""
        return costs.get_cost_by_dimension(principal.tenant_id, "prompt_version", date_str=date)

    @router.get(
        "/costs/anomaly",
        summary="Report cost anomaly status vs recent baseline",
        description=(
            "Compare today's spend against the tenant's average daily cost over "
            "the baseline window (default 7 days); an anomaly fires when today "
            "exceeds the configured factor (default 2x). Requires ``admin:manage``."
        ),
    )
    def cost_anomaly(
        principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
    ) -> dict:
        """Report whether today's cost exceeds the tenant's recent baseline."""
        return costs.check_anomaly(principal.tenant_id)

    return router
