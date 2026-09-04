"""OIDC / BFF session routes (Phase 27.2, hardened by M0 SEC-001).

Every login is a one-time, server-side verified transaction: ``state``,
``nonce``, PKCE S256 verifier, redirect URI and an optional tenant hint are
bound in ``auth_transactions`` and consumed exactly once on callback.  The ID
token is signature-verified (RS256/JWKS) with issuer/audience/exp/iat/nonce
enforcement, and tenant/actor/role come only from the local ``tenant_members``
roster — there is no caller-controlled fallback to ``demo``/``admin``.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from app.routers.common import RouteDeps
from app.security import SlidingWindowRateLimiter
from app.session_auth import (
    SESSION_COOKIE_NAME,
    renew_session_cookie,
    verify_session_cookie,
)

logger = logging.getLogger("helix")


def _require_same_origin(request: Request, allowed_origins: tuple[str, ...]) -> None:
    """CSRF guard (Phase 28.4): reject cross-origin state-changing requests.

    The BFF session cookie is SameSite=lax; for defense in depth, every
    state-changing BFF endpoint also checks the Origin/Referer header against
    the configured CORS origins (or same-host when none are configured).
    """
    from urllib.parse import urlsplit

    origin = request.headers.get("Origin") or request.headers.get("Referer")
    if not origin:
        # Same-site navigation without an Origin header is accepted; a
        # cross-site form POST always carries Origin or Referer.
        return
    try:
        parts = urlsplit(origin)
        origin_host = parts.hostname or ""
    except ValueError:
        raise HTTPException(status_code=403, detail="Cross-origin request rejected")
    if not origin_host:
        raise HTTPException(status_code=403, detail="Cross-origin request rejected")
    host = request.url.hostname or ""
    # Exact host match (with dot boundary) — a substring match would let
    # ``https://evil-<host>.com`` or ``<host>.evil.com`` bypass the guard.
    if host and (origin_host == host or origin_host.endswith("." + host)):
        return
    if allowed_origins:
        for allowed in allowed_origins:
            try:
                allowed_parts = urlsplit(allowed)
            except ValueError:
                continue
            allowed_host = allowed_parts.hostname or ""
            if allowed_host and origin_host == allowed_host:
                return
    raise HTTPException(status_code=403, detail="Cross-origin request rejected")


def _auth_rate_limit(
    request: Request,
    limiter: Any,
    limit_per_minute: int,
) -> None:
    """Independent rate limit for auth endpoints (Phase 28.4), keyed by IP."""
    if limiter is None:
        return
    client_ip = request.client.host if request.client else "unknown"
    allowed, _remaining, retry_after = limiter.check(f"auth:{client_ip}")
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many auth requests",
            headers={"Retry-After": str(retry_after)},
        )


def _request_id(request: Request) -> str:
    from app.context import current_request_id

    return current_request_id.get() or "-"


def _flow_error_status(exc: Exception) -> int:
    from app.oidc_flow import OIDCFlowError

    if isinstance(exc, OIDCFlowError):
        return exc.status_code
    return 500


def build_router(deps: RouteDeps) -> APIRouter:
    router = APIRouter()
    settings = deps.settings
    oidc_config = deps.oidc_config
    oidc_authenticator = deps.oidc_authenticator
    oidc_flow = deps.oidc_flow
    allowed_origins = settings.cors_origins
    # Phase 28.4: independent auth limiter (separate from the global per-key
    # limiter) to blunt brute-force on login/refresh.
    auth_limiter = SlidingWindowRateLimiter(settings.auth_rate_limit_per_minute)

    @router.get("/auth/login")
    async def auth_login(
        request: Request,
        tenant: str | None = None,
    ) -> RedirectResponse:
        if not oidc_flow or not oidc_config:
            raise HTTPException(status_code=501, detail="Session auth is not enabled")
        _auth_rate_limit(request, auth_limiter, settings.auth_rate_limit_per_minute)
        try:
            url, _tx = await oidc_flow.build_authorization_url(tenant_hint=tenant)
        except Exception as exc:
            logger.error("auth login request_id=%s failed: %s", _request_id(request), exc)
            raise HTTPException(
                status_code=_flow_error_status(exc),
                detail="Login could not be started",
            ) from exc
        return RedirectResponse(url=url, status_code=302)

    @router.get("/auth/callback")
    async def auth_callback(
        request: Request,
        code: str,
        state: str,
    ) -> RedirectResponse:
        if not oidc_flow or not oidc_config or not oidc_authenticator:
            raise HTTPException(status_code=501, detail="Session auth is not enabled")
        if not state or not code:
            raise HTTPException(status_code=400, detail="Missing OIDC state or code")
        try:
            identity = await oidc_flow.complete_login(
                state=state,
                code=code,
                redirect_uri=oidc_config.redirect_uri,
            )
        except Exception as exc:
            detail = getattr(exc, "public_message", None) or "Login failed"
            logger.error(
                "auth callback request_id=%s rejected: %s",
                _request_id(request),
                getattr(exc, "internal_detail", None) or exc,
            )
            raise HTTPException(status_code=_flow_error_status(exc), detail=detail) from exc

        _principal, cookie = oidc_authenticator.create_session(
            identity.tenant_id,
            identity.actor_id,
            identity.role,
        )
        response = RedirectResponse(url="/", status_code=302)
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=cookie,
            httponly=True,
            secure=settings.is_production,
            samesite="lax",
            max_age=oidc_config.session_ttl_minutes * 60,
        )
        return response

    @router.post("/auth/logout")
    async def auth_logout(request: Request, response: Response) -> dict[str, str]:
        # Phase 28.4 CSRF guard + independent auth rate limit.
        _require_same_origin(request, allowed_origins)
        _auth_rate_limit(request, auth_limiter, settings.auth_rate_limit_per_minute)
        response.delete_cookie(SESSION_COOKIE_NAME)
        return {"status": "logged_out"}

    @router.get("/auth/session")
    def auth_session(request: Request) -> dict[str, Any]:
        if not oidc_config:
            return {"authenticated": False, "reason": "session_auth_disabled"}
        cookie_value = request.cookies.get(SESSION_COOKIE_NAME, "")
        principal = verify_session_cookie(cookie_value, oidc_config)
        if principal is None:
            return {"authenticated": False}
        return {
            "authenticated": True,
            "tenant_id": principal.tenant_id,
            "actor_id": principal.actor_id,
            "role": principal.role,
            "expires_at": principal.expires_at,
        }

    @router.post("/auth/refresh")
    def auth_refresh(request: Request, response: Response) -> dict[str, Any]:
        """Renew the session cookie (sliding expiry) for an active session.

        The BFF holds no server-side session state, so renewal reissues the
        cookie with a fresh ``expires_at`` for the same session id, up to the
        configured absolute lifetime.  A missing, expired, or over-lifetime
        session gets ``401`` and the client must run the login flow again.
        """
        # Phase 28.4 CSRF guard + independent auth rate limit.
        _require_same_origin(request, allowed_origins)
        _auth_rate_limit(request, auth_limiter, settings.auth_rate_limit_per_minute)
        if not oidc_config:
            raise HTTPException(status_code=501, detail="Session auth is not enabled")
        cookie_value = request.cookies.get(SESSION_COOKIE_NAME, "")
        renewed = renew_session_cookie(cookie_value, oidc_config)
        if renewed is None:
            raise HTTPException(status_code=401, detail="Session expired or invalid")
        principal, cookie = renewed
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=cookie,
            httponly=True,
            secure=settings.is_production,
            samesite="lax",
            max_age=oidc_config.session_ttl_minutes * 60,
        )
        return {
            "authenticated": True,
            "session_id": principal.session_id,
            "tenant_id": principal.tenant_id,
            "actor_id": principal.actor_id,
            "role": principal.role,
            "expires_at": principal.expires_at,
        }

    return router
