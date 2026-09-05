"""Request controls and Problem Details exception handlers (Phase 27.2 home).

Extracted from ``app/main.py`` when the 2.x wiring pushed the composition
root past its post-Phase-27 size: the request-id/security-header/shadow-
traffic middleware and the versioned Problem Details handlers are one
cross-cutting concern, registered by :func:`register_request_controls` and
:func:`register_error_handlers` at the same point of ``create_app`` as when
they were defined inline. Behavior is verbatim; only the home moved.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Sequence
from time import perf_counter
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.assets import STATIC_ASSET_VERSION, VERSIONED_STATIC_CACHE_CONTROL
from app.audit_gap import AuditUnavailableError
from app.context import current_tenant, request_id_context
from app.deprecation import apply_deprecation_headers as _apply_deprecation_headers
from app.errors import (
    conflict_response,
    not_found_response,
    problem_response,
)
from app.orchestrator import (
    IdempotencyConflictError,
    InvalidTransitionError,
    TurnInProgressError,
)
from app.queue import QueueUnavailableError
from app.telemetry import span
from app.telemetry import metrics as telemetry_metrics

logger = logging.getLogger("helix")

REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


def _http_exception_code(status_code: int) -> str:
    """Map an HTTP status to a stable Problem Details code slug (Phase 25.1)."""
    return {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        409: "conflict",
        422: "unprocessable",
        429: "rate_limited",
    }.get(status_code, "http_error")


def _json_safe_errors(errors: Sequence[Any]) -> list[dict[str, Any]]:
    """Make FastAPI validation errors JSON-serializable (Phase 25.1).

    Pydantic's ``ctx`` may embed the exception object raised by a model
    validator (e.g. ``ValueError``), and its ``input`` field may carry the
    raw request body (``bytes`` when Content-Type was missing) — replace any
    non-serializable value with its string form so the 422 body renders.
    """
    cleaned: list[dict[str, Any]] = []
    for error in errors:
        item = dict(error)
        for key, value in item.items():
            item[key] = _json_safe_value(value)
        cleaned.append(item)
    return cleaned


def _json_safe_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", errors="replace")
        except Exception:
            return repr(value)
    return str(value)


def register_request_controls(
    app: FastAPI,
    *,
    settings: Any,
    database: Any,
    services: Any,
) -> None:
    """Register the request-id/security-header/shadow-traffic middleware."""

    @app.middleware("http")
    async def request_controls(request: Request, call_next: Callable[..., Any]) -> Response:
        supplied_id = request.headers.get("X-Request-Id", "")
        request_id = (
            supplied_id if REQUEST_ID_PATTERN.fullmatch(supplied_id) else f"req_{uuid4().hex}"
        )
        token = request_id_context.set(request_id)
        started = perf_counter()
        status_code = 500
        route = request.url.path
        with span(
            "http.request",
            method=request.method,
            path=request.url.path,
            request_id=request_id,
        ) as request_span:
            try:
                response = await call_next(request)
                status_code = response.status_code
                # ROADMAP 2.1.x: sampled read traffic is replayed to v2 in
                # the background. The v1 response body is approximated from
                # the framework-level status (response_model serialization
                # happens after the middleware) — comparison fidelity is
                # best-effort by design; the shadow task never blocks v1.
                # Requests carrying X-Shadow-Request (the shadow replay itself)
                # are never shadowed again, avoiding a self-replay loop.
                if (
                    settings.shadow_traffic_enabled
                    and request.headers.get("X-Shadow-Request") != "true"
                ):
                    try:
                        from app.shadow_traffic import maybe_shadow_request

                        maybe_shadow_request(
                            settings=settings,
                            db=database,
                            method=request.method,
                            path=request.url.path,
                            headers=dict(request.headers),
                            body=None,
                            v1_status_code=status_code,
                            v1_response_body={"status": "ok"},
                            v1_latency_ms=int((perf_counter() - started) * 1000),
                            tenant_id=current_tenant() or "demo",
                            request_id=request_id,
                        )
                    except Exception:
                        logger.exception("shadow.middleware_failed")
                is_widget_document = request.url.path.rstrip("/") == "/widget"
                response.headers["X-Request-Id"] = request_id
                response.headers["X-Content-Type-Options"] = "nosniff"
                if not is_widget_document:
                    response.headers["X-Frame-Options"] = "DENY"
                response.headers["Referrer-Policy"] = "no-referrer"
                response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
                frame_ancestors = (
                    " ".join(settings.widget_frame_ancestors) if is_widget_document else "'none'"
                )
                response.headers["Content-Security-Policy"] = (
                    "default-src 'self'; script-src 'self'; "
                    "style-src 'self'; "
                    "font-src 'self'; "
                    "img-src 'self' data:; connect-src 'self'; "
                    f"frame-ancestors {frame_ancestors}"
                )
                # 43.3: deprecated operations advertise their removal window on
                # every response (docs/API_POLICY.md §2). The route object in
                # the scope resolves templated paths ("/api/items/{id}") to the
                # registry's operation key shape.
                route_object_for_deprecation = request.scope.get("route")
                if route_object_for_deprecation is not None:
                    _apply_deprecation_headers(
                        f"{request.method} {getattr(route_object_for_deprecation, 'path', '')}",
                        response.headers,
                    )
                if request.url.path.startswith("/api/"):
                    response.headers["Cache-Control"] = "no-store"
                elif request.url.path in {
                    "/",
                    "/widget",
                    "/widget/",
                }:
                    response.headers["Cache-Control"] = "no-cache"
                elif request.url.path.startswith("/static/"):
                    response.headers["Cache-Control"] = (
                        VERSIONED_STATIC_CACHE_CONTROL
                        if request.query_params.get("v") == STATIC_ASSET_VERSION
                        else "no-cache"
                    )
                if settings.is_production:
                    response.headers["Strict-Transport-Security"] = (
                        "max-age=31536000; includeSubDomains"
                    )
                return response
            except Exception:
                logger.exception(
                    "request.failed",
                    extra={
                        "request_id": request_id,
                        "method": request.method,
                        "route": request.url.path,
                    },
                )
                raise
            finally:
                duration_ms = int((perf_counter() - started) * 1000)
                route_object = request.scope.get("route")
                route = getattr(route_object, "path", request.url.path)
                request_span.set_attribute("route", route)
                request_span.set_attribute("status_code", status_code)
                services.metrics.observe_request(route, status_code, duration_ms)
                telemetry_metrics.observe(
                    "http_request_duration_ms", duration_ms, route=route, method=request.method
                )
                telemetry_metrics.increment(
                    "http_requests_total",
                    route=route,
                    method=request.method,
                    status=str(status_code),
                )
                logger.info(
                    "request.completed",
                    extra={
                        "request_id": request_id,
                        "method": request.method,
                        "route": route,
                        "status_code": status_code,
                        "duration_ms": duration_ms,
                    },
                )
                request_id_context.reset(token)


def register_error_handlers(app: FastAPI) -> None:
    """Register the Problem Details exception handlers."""

    @app.exception_handler(LookupError)
    async def lookup_error_handler(request: Request, exc: LookupError) -> JSONResponse:
        return not_found_response(request, str(exc))

    @app.exception_handler(TurnInProgressError)
    async def turn_in_progress_handler(request: Request, exc: TurnInProgressError) -> JSONResponse:
        return conflict_response(
            request,
            str(exc),
            code="turn_in_progress",
            headers={"Retry-After": "1"},
        )

    @app.exception_handler(IdempotencyConflictError)
    async def idempotency_conflict_handler(
        request: Request, exc: IdempotencyConflictError
    ) -> JSONResponse:
        return conflict_response(request, str(exc), code="idempotency_conflict")

    @app.exception_handler(InvalidTransitionError)
    async def transition_error_handler(
        request: Request, exc: InvalidTransitionError
    ) -> JSONResponse:
        return conflict_response(request, str(exc), code="invalid_transition")

    @app.exception_handler(QueueUnavailableError)
    async def queue_unavailable_handler(
        request: Request, exc: QueueUnavailableError
    ) -> JSONResponse:
        # M0 REL-001: fail-closed deployments answer 503 with a retry window
        # while the task queue backend is unreachable, instead of silently
        # accepting work that can never be processed.
        return problem_response(
            request,
            status_code=503,
            detail=str(exc),
            code="queue_unavailable",
            headers={"Retry-After": "30"},
        )

    @app.exception_handler(AuditUnavailableError)
    async def audit_unavailable_handler(
        request: Request, exc: AuditUnavailableError
    ) -> JSONResponse:
        # Phase 41.3 SEC-005: a high-risk mutation (security / permission /
        # credential / DSR) must never appear to succeed with its audit
        # evidence lost; the mutation itself rolled back, so answer fail-closed
        # with a retry window rather than a misleading success.
        return problem_response(
            request,
            status_code=503,
            detail=str(exc),
            code="audit_unavailable",
            headers={"Retry-After": "30"},
        )

    def _versioned_problem_response(request: Request, **kwargs: Any) -> JSONResponse:
        response = problem_response(request, **kwargs)
        # 43.3: v2 responses always disclose their API version, errors included.
        if request.url.path.startswith("/api/v2/"):
            response.headers["X-API-Version"] = "2.0"
        return response

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        # FastAPI's default HTTPException renders {"detail": ...}; route code
        # raises HTTPException for 400/401/403/404/409/422/429. Normalize every
        # one to Problem Details while keeping the detail field intact.
        return _versioned_problem_response(
            request,
            status_code=exc.status_code,
            detail=str(exc.detail),
            code=_http_exception_code(exc.status_code),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # 422 with per-field details; the body retains FastAPI's structured
        # error list under "errors" plus the Problem Details envelope.
        return _versioned_problem_response(
            request,
            status_code=422,
            detail="Request validation failed",
            code="validation_error",
            errors=_json_safe_errors(exc.errors()),
        )
