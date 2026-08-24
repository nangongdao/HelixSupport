"""OIDC + BFF (Backend-For-Frontend) session authentication.

Provides an HTTP-only cookie session layer alongside the existing API Key
authentication.  The BFF pattern keeps token exchange server-side so that
browser clients never see access/refresh tokens.

Authorization-code flow hardening (state/nonce/PKCE transaction binding, JWKS
signature verification, issuer/audience/time/nonce enforcement and roster-based
identity mapping) lives in :mod:`app.oidc_flow`; this module owns only the
signed session cookie: creation, rolling-key verification, sliding renewal and
destruction.

Configuration:
- ``AUTH_MODE=oidc`` – enables the session flow.
- ``OIDC_CLIENT_ID``, ``OIDC_CLIENT_SECRET`` – OIDC client credentials.
- ``OIDC_DISCOVERY_URL`` – issuer well-known endpoint.
- ``OIDC_REDIRECT_URI`` – callback URL.
- ``SESSION_SECRET`` – secret for signing session cookies.
- ``SESSION_TTL_MINUTES`` – session lifetime (default 480 = 8h).

Usage:
    authenticator = OIDCAuthenticator(settings)
    # On callback, use app.oidc_flow.OIDCFlow to verify the exchange, then:
    principal, cookie = authenticator.create_session(tenant_id, actor_id, role)
    # Subsequent requests are authenticated via the session cookie.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "helix_session"
DEFAULT_SESSION_TTL_MINUTES = 480


@dataclass(frozen=True)
class SessionPrincipal:
    """Authenticated principal from an OIDC session."""

    tenant_id: str
    actor_id: str
    role: str
    session_id: str
    expires_at: int

    @property
    def is_expired(self) -> bool:
        return time.time() >= self.expires_at


@dataclass
class OIDCConfig:
    client_id: str = ""
    client_secret: str = ""
    discovery_url: str = ""
    redirect_uri: str = ""
    session_secret: str = ""
    session_secrets_json: str = ""
    session_active_kid: str = "1"
    session_ttl_minutes: int = DEFAULT_SESSION_TTL_MINUTES
    session_max_lifetime_minutes: int = 0
    scopes: tuple[str, ...] = ("openid", "profile", "email")

    @classmethod
    def from_env(cls) -> "OIDCConfig":
        return cls(
            client_id=os.getenv("OIDC_CLIENT_ID", ""),
            client_secret=os.getenv("OIDC_CLIENT_SECRET", ""),
            discovery_url=os.getenv("OIDC_DISCOVERY_URL", ""),
            redirect_uri=os.getenv("OIDC_REDIRECT_URI", ""),
            session_secret=os.getenv("SESSION_SECRET", ""),
            session_secrets_json=os.getenv("SESSION_SECRETS_JSON", ""),
            session_active_kid=os.getenv("SESSION_ACTIVE_KID", "1"),
            session_ttl_minutes=int(
                os.getenv("SESSION_TTL_MINUTES", str(DEFAULT_SESSION_TTL_MINUTES))
            ),
            session_max_lifetime_minutes=int(os.getenv("SESSION_MAX_LIFETIME_MINUTES", "0")),
            scopes=tuple(
                s.strip() for s in os.getenv("OIDC_SCOPES", "openid profile email").split()
            ),
        )

    def session_key_map(self) -> dict[str, str]:
        """Resolve the session signing keys as ``{kid: secret}`` (Phase 28.2).

        ``SESSION_SECRETS_JSON`` (a JSON object keyed by kid, newest first)
        enables rolling rotation: old kids stay valid for verification while
        only ``session_active_kid`` signs new cookies. Without it, the legacy
        single ``SESSION_SECRET`` is the sole key under kid "1".
        """
        raw = (self.session_secrets_json or "").strip()
        if raw:
            try:
                parsed = json.loads(raw)
            except ValueError as exc:
                raise ValueError("SESSION_SECRETS_JSON must be valid JSON") from exc
            if not isinstance(parsed, dict) or not parsed:
                raise ValueError("SESSION_SECRETS_JSON must be a non-empty object")
            if not all(isinstance(v, str) and v for v in parsed.values()):
                raise ValueError("SESSION_SECRETS_JSON values must be non-empty strings")
            return {str(k): str(v) for k, v in parsed.items()}
        return {"1": self.session_secret}

    @property
    def is_configured(self) -> bool:
        return bool(
            self.client_id
            and self.client_secret
            and self.discovery_url
            and (self.session_secret or self.session_secrets_json)
        )


def create_session_cookie(
    principal: SessionPrincipal,
    config: OIDCConfig,
    *,
    issued_at: int | None = None,
) -> str:
    """Create a signed, tamper-proof session cookie value (Phase 28.2).

    The payload carries the signing key id (``kid``) so a rotated secret can
    verify old cookies during the rolling window while new cookies are signed
    with the active key.
    """
    kid = config.session_active_kid
    key_map = config.session_key_map()
    secret = key_map.get(kid)
    if secret is None:
        raise ValueError(f"SESSION_ACTIVE_KID '{kid}' not in SESSION_SECRETS_JSON")
    payload = {
        "kid": kid,
        "sid": principal.session_id,
        "tid": principal.tenant_id,
        "uid": principal.actor_id,
        "role": principal.role,
        "exp": principal.expires_at,
        "iat": issued_at if issued_at is not None else int(time.time()),
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    b64 = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    signature = _sign(b64, secret)
    return f"{b64}.{signature}"


def _cookie_issued_at(b64: str, config: OIDCConfig, ttl_seconds: int) -> int:
    """Best-effort original-issuance time from the cookie payload.

    Cookies issued before the ``iat`` claim existed do not carry it; for those,
    derive the issuance from the expiry minus the TTL.
    """
    try:
        padded = b64 + "=" * (-len(b64) % 4)
        payload: Any = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("iat"), int):
            return payload["iat"]
    except (ValueError, json.JSONDecodeError) as exc:
        logger.warning("Session cookie decode error: %s", exc)
    return int(time.time()) - ttl_seconds


def renew_session_cookie(
    cookie_value: str,
    config: OIDCConfig,
) -> tuple[SessionPrincipal, str] | None:
    """Reissue a valid session cookie with a fresh expiry (sliding renewal).

    Returns ``(principal, cookie)`` when the current session is still valid and
    inside its absolute lifetime cap, otherwise ``None``.  The renewed cookie
    keeps the same session id and identity but extends ``expires_at`` by the
    configured TTL, so an operator is not logged out mid-shift; a genuinely
    expired session, or one beyond ``session_max_lifetime_minutes``, still
    fails and forces a fresh login.
    """
    principal = verify_session_cookie(cookie_value, config)
    if principal is None:
        return None
    issued_at = _cookie_issued_at(
        cookie_value.rpartition(".")[0], config, config.session_ttl_minutes * 60
    )
    if (
        config.session_max_lifetime_minutes > 0
        and time.time() - issued_at > config.session_max_lifetime_minutes * 60
    ):
        logger.info("Session beyond max lifetime; renewal refused")
        return None
    renewed = SessionPrincipal(
        tenant_id=principal.tenant_id,
        actor_id=principal.actor_id,
        role=principal.role,
        session_id=principal.session_id,
        expires_at=int(time.time()) + config.session_ttl_minutes * 60,
    )
    return renewed, create_session_cookie(renewed, config, issued_at=issued_at)


def generate_session_id() -> str:
    return secrets.token_urlsafe(32)


def generate_state() -> str:
    return secrets.token_urlsafe(16)


def verify_session_cookie(
    cookie_value: str,
    config: OIDCConfig,
) -> SessionPrincipal | None:
    """Verify and decode a session cookie. Returns None if invalid or expired.

    Supports rolling key rotation (Phase 28.2): the payload's ``kid`` selects
    the verification secret from the key map, so a cookie signed with a
    rotated-out key still verifies while that key remains in the map.
    """
    if not cookie_value or "." not in cookie_value:
        return None
    b64, _, signature = cookie_value.rpartition(".")
    if not b64 or not signature:
        return None
    try:
        padded = b64 + "=" * (-len(b64) % 4)
        payload: Any = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except (ValueError, json.JSONDecodeError) as exc:
        logger.warning("Session cookie decode error: %s", exc)
        return None
    if not isinstance(payload, dict):
        return None
    key_map = config.session_key_map()
    kid = payload.get("kid", "1")
    secret = key_map.get(kid)
    if secret is None:
        logger.warning("Session cookie kid %r not in key map", kid)
        return None
    expected = _sign(b64, secret)
    if not hmac.compare_digest(signature, expected):
        logger.warning("Session cookie signature mismatch")
        return None
    expires_at = payload.get("exp", 0)
    if time.time() >= expires_at:
        logger.debug("Session cookie expired")
        return None
    return SessionPrincipal(
        tenant_id=payload.get("tid", ""),
        actor_id=payload.get("uid", ""),
        role=payload.get("role", ""),
        session_id=payload.get("sid", ""),
        expires_at=expires_at,
    )


def _sign(data: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), data.encode("utf-8"), hashlib.sha256).hexdigest()


class OIDCAuthenticator:
    """Create and destroy signed BFF session cookies.

    Verification of the OIDC exchange itself happens in :mod:`app.oidc_flow`;
    this class only turns a verified identity into the HttpOnly session cookie
    and back out again.
    """

    def __init__(self, config: OIDCConfig) -> None:
        self.config = config

    def create_session(
        self,
        tenant_id: str,
        actor_id: str,
        role: str,
    ) -> tuple[SessionPrincipal, str]:
        """Create a signed session for an authenticated user."""
        principal = SessionPrincipal(
            tenant_id=tenant_id,
            actor_id=actor_id,
            role=role,
            session_id=generate_session_id(),
            expires_at=int(time.time()) + self.config.session_ttl_minutes * 60,
        )
        cookie = create_session_cookie(principal, self.config)
        return principal, cookie

    def destroy_session(self) -> str:
        """Return an expired cookie to clear the session."""
        return ""
