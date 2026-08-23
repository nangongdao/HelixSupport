"""Phase 21.1 quality supervisor API.

GET /api/supervisor/quality returns keyset-paginated buckets with
escalalation/negative-feedback rates, average first-response and latency.
RBAC: ``metrics:read`` for viewer/supervisor/admin.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from typing_extensions import Annotated

from app.main import AppServices, require_permission
from app.quality import (
    decode_quality_cursor,
    encode_quality_cursor,
)
from app.pagination import InvalidCursorError
from app.security import Principal
from app.schemas import QualityBucketOut

router = APIRouter(prefix="/api/supervisor", tags=["quality"])


def _services(request: Request) -> AppServices:
    services: AppServices | None = getattr(request.app.state, "services", None)
    if services is None:
        raise HTTPException(500, "services not initialized")
    return services


@router.get("/quality", response_model=list[QualityBucketOut])
def list_quality_buckets(
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(require_permission("metrics:read"))],
    since: Annotated[str | None, Query(max_length=10)] = None,
    until: Annotated[str | None, Query(max_length=10)] = None,
    intent: Annotated[str | None, Query(max_length=80)] = None,
    prompt_version: Annotated[str | None, Query(max_length=80)] = None,
    cursor: Annotated[str | None, Query(max_length=1024)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[dict[str, Any]]:
    services = _services(request)
    quality = services.quality
    if quality is None:
        raise HTTPException(500, "quality service not initialized")

    try:
        decoded = decode_quality_cursor(cursor) if cursor else None
    except InvalidCursorError as exc:
        raise HTTPException(400, str(exc)) from exc

    try:
        buckets = quality.list_buckets(
            principal.tenant_id,
            since=since,
            until=until,
            intent=intent,
            prompt_version=prompt_version,
            cursor=decoded,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if buckets:
        last = buckets[-1]
        next_cursor = encode_quality_cursor(last["date"], last["intent"], last["prompt_version"])
        response.headers["X-Next-Cursor"] = next_cursor
    response.headers["X-Has-More"] = str(len(buckets) == limit).lower()
    return buckets
