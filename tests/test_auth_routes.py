"""Integration tests for auth router endpoints (app/routers/auth.py).

Tests cover the FastAPI route layer that wraps the OIDC flow, including:
- Login initiation and callback handling
- CSRF protection via same-origin checks
- Session management (logout, session info, refresh)
- Rate limiting on auth endpoints
- Error handling and edge cases
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import Mock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.routers.auth import _require_same_origin, build_router
from app.routers.common import RouteDeps
from app.session_auth import SESSION_COOKIE_NAME, SessionPrincipal


# Mock settings
class MockSettings:
    def __init__(self):
        self.cors_origins = ()
        self.is_production = False
        self.auth_rate_limit_per_minute = 60


class MockOIDCConfig:
    def __init__(self):
        self.redirect_uri = "http://localhost:8000/auth/callback"
        self.session_ttl_minutes = 60
        self.session_absolute_ttl_minutes = 480


class MockIdentity:
    def __init__(self, tenant_id: str = "tenant-1", actor_id: str = "user-1", role: str = "admin"):
        self.tenant_id = tenant_id
        self.actor_id = actor_id
        self.role = role


class MockOIDCFlow:
    async def build_authorization_url(self, tenant_hint: str | None = None):
        return ("https://idp.example.com/auth?state=test-state", {"state": "test-state"})

    async def complete_login(self, state: str, code: str, redirect_uri: str):
        if state == "invalid":
            from app.oidc_flow import OIDCFlowError
            raise OIDCFlowError("Login failed", status_code=400)
        return MockIdentity()


class MockAuthenticator:
    def create_session(self, tenant_id: str, actor_id: str, role: str):
        principal = SessionPrincipal(
            session_id="session-123",
            tenant_id=tenant_id,
            actor_id=actor_id,
            role=role,
            expires_at=int(time.time()) + 3600,
        )
        return principal, "mock-cookie-value"


def _build_test_app(
    settings: Any = None,
    oidc_config: Any = None,
    oidc_flow: Any = None,
    oidc_authenticator: Any = None,
) -> FastAPI:
    """Build a minimal FastAPI app with auth router for testing."""
    app = FastAPI()
    deps = RouteDeps(
        settings=settings or MockSettings(),
        database=Mock(),
        orchestrator=Mock(),
        turn_worker=Mock(),
        services=Mock(),
        queue=Mock(),
        webhook_service=None,
        static_dir=Mock(),
        oidc_config=oidc_config,
        oidc_authenticator=oidc_authenticator,
        oidc_flow=oidc_flow,
    )
    router = build_router(deps)
    app.include_router(router)
    return app


def test_login_disabled_when_no_oidc_config():
    """Login endpoint returns 501 when OIDC is not configured."""
    app = _build_test_app(oidc_config=None, oidc_flow=None)
    client = TestClient(app)

    response = client.get("/auth/login")

    assert response.status_code == 501
    assert "not enabled" in response.json()["detail"]


def test_login_returns_redirect():
    """Login endpoint returns redirect to IdP authorization URL."""
    app = _build_test_app(
        oidc_config=MockOIDCConfig(),
        oidc_flow=MockOIDCFlow(),
    )
    client = TestClient(app, follow_redirects=False)

    response = client.get("/auth/login")

    assert response.status_code == 302
    assert response.headers["location"].startswith("https://idp.example.com/auth")


def test_login_with_tenant_hint():
    """Login endpoint accepts optional tenant query parameter."""
    flow = MockOIDCFlow()
    app = _build_test_app(
        oidc_config=MockOIDCConfig(),
        oidc_flow=flow,
    )
    client = TestClient(app, follow_redirects=False)

    with patch.object(flow, "build_authorization_url", return_value=("https://idp.example.com/auth", {})) as mock_build:
        response = client.get("/auth/login?tenant=tenant-123")

        assert response.status_code == 302
        mock_build.assert_called_once()
        call_kwargs = mock_build.call_args[1]
        assert call_kwargs["tenant_hint"] == "tenant-123"


def test_callback_disabled_when_no_oidc_config():
    """Callback endpoint returns 501 when OIDC is not configured."""
    app = _build_test_app(oidc_config=None, oidc_flow=None)
    client = TestClient(app)

    response = client.get("/auth/callback?code=abc&state=xyz")

    assert response.status_code == 501


def test_callback_requires_state_and_code():
    """Callback endpoint returns 422 if state or code is missing."""
    app = _build_test_app(
        oidc_config=MockOIDCConfig(),
        oidc_flow=MockOIDCFlow(),
        oidc_authenticator=MockAuthenticator(),
    )
    client = TestClient(app)

    response = client.get("/auth/callback?code=abc")
    assert response.status_code == 422  # FastAPI validation error

    response = client.get("/auth/callback?state=xyz")
    assert response.status_code == 422


def test_callback_success_sets_session_cookie():
    """Successful callback sets httponly session cookie and redirects to root."""
    app = _build_test_app(
        oidc_config=MockOIDCConfig(),
        oidc_flow=MockOIDCFlow(),
        oidc_authenticator=MockAuthenticator(),
    )
    client = TestClient(app, follow_redirects=False)

    response = client.get("/auth/callback?code=valid-code&state=valid-state")

    assert response.status_code == 302
    assert response.headers["location"] == "/"

    # Check cookie attributes
    set_cookie = response.headers["set-cookie"]
    assert SESSION_COOKIE_NAME in set_cookie
    assert "mock-cookie-value" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie


def test_callback_failure_returns_error():
    """Failed callback (invalid state) returns error with public message."""
    app = _build_test_app(
        oidc_config=MockOIDCConfig(),
        oidc_flow=MockOIDCFlow(),
        oidc_authenticator=MockAuthenticator(),
    )
    client = TestClient(app)

    # Mock the current_request_id function
    with patch("app.context.current_request_id", return_value="test-request-123"):
        response = client.get("/auth/callback?code=any&state=invalid")

    assert response.status_code == 400
    assert "Login failed" in response.json()["detail"]


def test_logout_deletes_cookie():
    """Logout endpoint deletes the session cookie."""
    app = _build_test_app()
    client = TestClient(app)

    response = client.post("/auth/logout")

    assert response.status_code == 200
    assert response.json()["status"] == "logged_out"

    # Check cookie deletion
    set_cookie = response.headers.get("set-cookie", "")
    assert SESSION_COOKIE_NAME in set_cookie
    # Cookie deletion is indicated by Max-Age=0 or expires in the past
    assert "Max-Age=0" in set_cookie or "expires=" in set_cookie


def test_session_returns_unauthenticated_when_no_config():
    """Session endpoint returns unauthenticated when OIDC is disabled."""
    app = _build_test_app(oidc_config=None)
    client = TestClient(app)

    response = client.get("/auth/session")

    assert response.status_code == 200
    data = response.json()
    assert data["authenticated"] is False
    assert data["reason"] == "session_auth_disabled"


def test_session_returns_unauthenticated_when_no_cookie():
    """Session endpoint returns unauthenticated when no session cookie present."""
    app = _build_test_app(oidc_config=MockOIDCConfig())
    client = TestClient(app)

    response = client.get("/auth/session")

    assert response.status_code == 200
    data = response.json()
    assert data["authenticated"] is False


def test_session_returns_principal_when_valid_cookie():
    """Session endpoint returns principal info when valid session cookie present."""
    app = _build_test_app(oidc_config=MockOIDCConfig())
    client = TestClient(app)

    # Mock a valid session cookie verification
    with patch("app.routers.auth.verify_session_cookie") as mock_verify:
        mock_verify.return_value = SessionPrincipal(
            session_id="sess-123",
            tenant_id="tenant-1",
            actor_id="user-1",
            role="operator",
            expires_at=int(time.time()) + 1800,
        )

        response = client.get("/auth/session", cookies={SESSION_COOKIE_NAME: "valid-cookie"})

        assert response.status_code == 200
        data = response.json()
        assert data["authenticated"] is True
        assert data["tenant_id"] == "tenant-1"
        assert data["actor_id"] == "user-1"
        assert data["role"] == "operator"
        assert "expires_at" in data


def test_refresh_disabled_when_no_config():
    """Refresh endpoint returns 501 when OIDC is disabled."""
    app = _build_test_app(oidc_config=None)
    client = TestClient(app)

    response = client.post("/auth/refresh")

    assert response.status_code == 501


def test_refresh_returns_401_when_session_expired():
    """Refresh endpoint returns 401 when session cannot be renewed."""
    app = _build_test_app(oidc_config=MockOIDCConfig())
    client = TestClient(app)

    with patch("app.routers.auth.renew_session_cookie", return_value=None):
        response = client.post("/auth/refresh", cookies={SESSION_COOKIE_NAME: "expired"})

        assert response.status_code == 401
        assert "expired or invalid" in response.json()["detail"]


def test_refresh_renews_cookie_and_returns_principal():
    """Refresh endpoint renews session cookie and returns updated principal."""
    app = _build_test_app(oidc_config=MockOIDCConfig())
    client = TestClient(app)

    renewed_principal = SessionPrincipal(
        session_id="sess-123",
        tenant_id="tenant-1",
        actor_id="user-1",
        role="operator",
        expires_at=int(time.time()) + 3600,
    )

    with patch("app.routers.auth.renew_session_cookie", return_value=(renewed_principal, "new-cookie-value")):
        response = client.post("/auth/refresh", cookies={SESSION_COOKIE_NAME: "old-cookie"})

        assert response.status_code == 200
        data = response.json()
        assert data["authenticated"] is True
        assert data["session_id"] == "sess-123"
        assert data["tenant_id"] == "tenant-1"

        # Check new cookie is set
        set_cookie = response.headers["set-cookie"]
        assert "new-cookie-value" in set_cookie
        assert "HttpOnly" in set_cookie


def test_require_same_origin_accepts_same_host():
    """_require_same_origin accepts requests from same host."""
    request = Mock(spec=Request)
    request.headers = {"Origin": "http://localhost:8000"}
    request.url = Mock(hostname="localhost")

    # Should not raise
    _require_same_origin(request, ())


def test_require_same_origin_accepts_subdomain():
    """_require_same_origin accepts requests from subdomains."""
    request = Mock(spec=Request)
    request.headers = {"Origin": "https://app.example.com"}
    request.url = Mock(hostname="example.com")

    # Should not raise
    _require_same_origin(request, ())


def test_require_same_origin_rejects_different_host():
    """_require_same_origin rejects requests from different hosts."""
    from fastapi import HTTPException

    request = Mock(spec=Request)
    request.headers = {"Origin": "https://evil.com"}
    request.url = Mock(hostname="example.com")

    with pytest.raises(HTTPException) as exc_info:
        _require_same_origin(request, ())

    assert exc_info.value.status_code == 403
    assert "Cross-origin" in exc_info.value.detail


def test_require_same_origin_accepts_allowed_origin():
    """_require_same_origin accepts explicitly allowed CORS origins."""
    request = Mock(spec=Request)
    request.headers = {"Origin": "https://trusted.com"}
    request.url = Mock(hostname="api.example.com")

    # Should not raise with allowed_origins
    _require_same_origin(request, ("https://trusted.com",))


def test_require_same_origin_rejects_similar_but_different_host():
    """_require_same_origin rejects hosts that are similar but not subdomains."""
    from fastapi import HTTPException

    request = Mock(spec=Request)
    request.headers = {"Origin": "https://evil-example.com"}
    request.url = Mock(hostname="example.com")

    with pytest.raises(HTTPException):
        _require_same_origin(request, ())

    # Also test suffix attack
    request.headers = {"Origin": "https://example.com.evil.com"}
    with pytest.raises(HTTPException):
        _require_same_origin(request, ())


def test_require_same_origin_accepts_missing_origin_header():
    """_require_same_origin accepts requests without Origin (same-site navigation)."""
    request = Mock(spec=Request)
    request.headers = {}
    request.url = Mock(hostname="example.com")

    # Should not raise - same-site navigation is allowed
    _require_same_origin(request, ())


def test_require_same_origin_rejects_invalid_origin_format():
    """_require_same_origin rejects malformed Origin headers."""
    from fastapi import HTTPException

    request = Mock(spec=Request)
    request.headers = {"Origin": "not-a-valid-url:::///"}
    request.url = Mock(hostname="example.com")

    with pytest.raises(HTTPException) as exc_info:
        _require_same_origin(request, ())

    assert exc_info.value.status_code == 403


def test_require_same_origin_rejects_empty_origin_hostname():
    """_require_same_origin rejects Origin with empty hostname."""
    from fastapi import HTTPException

    request = Mock(spec=Request)
    request.headers = {"Origin": "http://"}
    request.url = Mock(hostname="example.com")

    with pytest.raises(HTTPException) as exc_info:
        _require_same_origin(request, ())

    assert exc_info.value.status_code == 403
