"""RFC 9457 Problem Details error contract (Phase 25.1).

Every API error response is a JSON Problem Details document:

    {
        "type": "about:blank" | "urn:helix:error:<code>",
        "title": "Not Found",
        "status": 404,
        "detail": "human-readable explanation",
        "instance": "/api/...",
        "request_id": "req_...",
        "code": "not_found",
        "errors": [ ... validation specifics, 422 only ... ]
    }

The legacy ``detail`` field is preserved (the top-level ``detail`` key) for
one minor release, per the API policy in ``docs/API_POLICY.md``; consumers
migrating to Problem Details read ``title``/``status``/``instance`` and the
``code`` extension instead.

Every handler returns ``JSONResponse`` directly so the body is always a
Problem Details document regardless of how the exception was raised
(``HTTPException`` from route code or one of the domain exceptions).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from app.context import current_request_id

# RFC 9457 section 4: "about:blank" when no further type information is given.
_BLANK = "about:blank"

# Standard titles per status code (RFC 9110 reason phrases).
_STATUS_TITLES: dict[int, str] = {
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    409: "Conflict",
    422: "Unprocessable Entity",
    429: "Too Many Requests",
    500: "Internal Server Error",
    502: "Bad Gateway",
    503: "Service Unavailable",
}


def problem_response(
    request: Request,
    *,
    status_code: int,
    detail: str,
    code: str,
    title: str | None = None,
    errors: Sequence[Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Build an RFC 9457 Problem Details response.

    ``code`` is a stable machine-readable slug (e.g. ``not_found``,
    ``invalid_transition``) that is also encoded into ``type`` as
    ``urn:helix:error:<code>`` so a client can dispatch on either field.
    """
    body: dict[str, Any] = {
        "type": f"urn:helix:error:{code}",
        "title": title or _STATUS_TITLES.get(status_code, "Error"),
        "status": status_code,
        "detail": detail,
        "instance": request.url.path,
        "request_id": current_request_id(),
        "code": code,
    }
    if errors:
        body["errors"] = errors
    return JSONResponse(status_code=status_code, content=body, headers=headers)


def not_found_response(request: Request, detail: str, *, code: str = "not_found") -> JSONResponse:
    return problem_response(request, status_code=404, detail=detail, code=code)


def conflict_response(
    request: Request, detail: str, *, code: str = "conflict", headers: dict[str, str] | None = None
) -> JSONResponse:
    return problem_response(request, status_code=409, detail=detail, code=code, headers=headers)


def bad_request_response(
    request: Request, detail: str, *, code: str = "bad_request"
) -> JSONResponse:
    return problem_response(request, status_code=400, detail=detail, code=code)


def forbidden_response(request: Request, detail: str, *, code: str = "forbidden") -> JSONResponse:
    return problem_response(request, status_code=403, detail=detail, code=code)


__all__ = [
    "bad_request_response",
    "conflict_response",
    "forbidden_response",
    "not_found_response",
    "problem_response",
]
