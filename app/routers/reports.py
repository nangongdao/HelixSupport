"""Report export & subscription routes (backlog: 报表导出与订阅).

Admin endpoints for scheduled report subscriptions (webhook delivery) and
on-demand generation/export of quality and usage reports. All routes require
``admin:manage`` — reports contain tenant-wide aggregates.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.main import require_permission
from app.reports import REPORT_TYPES, ReportService
from app.routers.common import RouteDeps
from app.schemas import (
    ReportGenerateOut,
    ReportGenerateRequest,
    ReportSubscriptionCreateRequest,
    ReportSubscriptionOut,
    ReportSubscriptionUpdateRequest,
)
from app.security import Principal


def build_router(deps: RouteDeps) -> APIRouter:
    router = APIRouter()
    reports: ReportService = deps.services.reports

    @router.post(
        "/api/admin/report-subscriptions",
        response_model=ReportSubscriptionOut,
        status_code=201,
    )
    def create_report_subscription(
        payload: ReportSubscriptionCreateRequest,
        principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
    ) -> ReportSubscriptionOut:
        try:
            subscription = reports.create_subscription(
                principal.tenant_id,
                report_type=payload.report_type,
                schedule=payload.schedule,
                webhook_endpoint_id=payload.webhook_endpoint_id,
                window_days=payload.window_days,
                actor_id=principal.actor_id,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return ReportSubscriptionOut(**subscription)

    @router.get("/api/admin/report-subscriptions", response_model=list[ReportSubscriptionOut])
    def list_report_subscriptions(
        principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
    ) -> list[ReportSubscriptionOut]:
        return [
            ReportSubscriptionOut(**item)
            for item in reports.list_subscriptions(principal.tenant_id)
        ]

    @router.patch(
        "/api/admin/report-subscriptions/{subscription_id}",
        response_model=ReportSubscriptionOut,
    )
    def update_report_subscription(
        subscription_id: str,
        payload: ReportSubscriptionUpdateRequest,
        principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
    ) -> ReportSubscriptionOut:
        changes = payload.model_dump(exclude_unset=True)
        try:
            subscription = reports.update_subscription(
                principal.tenant_id, subscription_id, changes, principal.actor_id
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return ReportSubscriptionOut(**subscription)

    @router.delete("/api/admin/report-subscriptions/{subscription_id}")
    def delete_report_subscription(
        subscription_id: str,
        principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
    ) -> dict[str, bool]:
        deleted = reports.delete_subscription(
            principal.tenant_id, subscription_id, principal.actor_id
        )
        if not deleted:
            raise HTTPException(status_code=404, detail="Report subscription not found")
        return {"deleted": True}

    @router.post("/api/admin/reports/generate", response_model=ReportGenerateOut)
    def generate_report(
        payload: ReportGenerateRequest,
        principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
    ) -> ReportGenerateOut:
        report = reports.generate(principal.tenant_id, payload.report_type, payload.window_days)
        deliveries = 0
        if payload.webhook_endpoint_id:
            try:
                deliveries = reports.deliver_report(
                    principal.tenant_id,
                    payload.webhook_endpoint_id,
                    report,
                    dedup_key="ondemand",
                )
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
        return ReportGenerateOut(**report, deliveries=deliveries)

    @router.get("/api/admin/reports/{report_type}/export")
    def export_report_csv(
        report_type: str,
        principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
        from_date: Annotated[str | None, Query(alias="from")] = None,
        to_date: Annotated[str | None, Query(alias="to")] = None,
    ) -> Response:
        if report_type not in REPORT_TYPES:
            raise HTTPException(
                status_code=422, detail=f"report_type must be one of {REPORT_TYPES}"
            )
        rows = reports.rows_for_range(principal.tenant_id, report_type, from_date, to_date)
        csv_text = reports.export_csv(report_type, rows)
        filename = f"report-{report_type}-{rows[0]['date'] if rows else 'empty'}.csv"
        return Response(
            content=csv_text,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return router
