"""Tests for app/widget_token.py signed customer tokens."""

from unittest import mock

import pytest

from app.widget_token import (
    WidgetTokenError,
    sign_token,
    verify_token,
)


def test_sign_and_verify_minimal_token():
    """Sign and verify a minimal token with only tenant_id."""
    secret = "test-secret-key"
    token = sign_token(secret=secret, tenant_id="tenant-123", now=1000)
    result = verify_token(secret=secret, token=token, now=1000)
    assert result.tenant_id == "tenant-123"
    assert result.customer_ref is None
    assert result.conversation_id is None
    assert result.iat == 1000
    assert result.exp == 1000 + 3600


def test_sign_and_verify_with_customer_ref():
    """Sign and verify token with customer_ref."""
    secret = "test-secret"
    token = sign_token(
        secret=secret,
        tenant_id="tenant-123",
        customer_ref="customer-456",
        now=1000,
    )
    result = verify_token(secret=secret, token=token, now=1000)
    assert result.tenant_id == "tenant-123"
    assert result.customer_ref == "customer-456"
    assert result.conversation_id is None


def test_sign_and_verify_with_conversation_id():
    """Sign and verify token with conversation_id."""
    secret = "test-secret"
    token = sign_token(
        secret=secret,
        tenant_id="tenant-123",
        conversation_id="conv-789",
        now=1000,
    )
    result = verify_token(secret=secret, token=token, now=1000)
    assert result.tenant_id == "tenant-123"
    assert result.customer_ref is None
    assert result.conversation_id == "conv-789"


def test_sign_and_verify_all_fields():
    """Sign and verify token with all optional fields."""
    secret = "test-secret"
    token = sign_token(
        secret=secret,
        tenant_id="tenant-123",
        customer_ref="customer-456",
        conversation_id="conv-789",
        ttl_seconds=7200,
        now=1000,
    )
    result = verify_token(secret=secret, token=token, now=1000)
    assert result.tenant_id == "tenant-123"
    assert result.customer_ref == "customer-456"
    assert result.conversation_id == "conv-789"
    assert result.iat == 1000
    assert result.exp == 1000 + 7200


def test_verify_rejects_expired_token():
    """Token past its exp timestamp is rejected."""
    secret = "test-secret"
    token = sign_token(secret=secret, tenant_id="tenant-123", ttl_seconds=100, now=1000)
    with pytest.raises(WidgetTokenError, match="expired"):
        verify_token(secret=secret, token=token, now=1200)


def test_verify_rejects_future_iat():
    """Token with iat in the future beyond max_skew is rejected."""
    secret = "test-secret"
    token = sign_token(secret=secret, tenant_id="tenant-123", now=2000)
    with pytest.raises(WidgetTokenError, match="issued in the future"):
        verify_token(secret=secret, token=token, now=1000, max_skew_seconds=100)


def test_verify_accepts_future_iat_within_skew():
    """Token with iat slightly in future within max_skew is accepted."""
    secret = "test-secret"
    token = sign_token(secret=secret, tenant_id="tenant-123", now=1100)
    result = verify_token(secret=secret, token=token, now=1000, max_skew_seconds=200)
    assert result.tenant_id == "tenant-123"


def test_verify_rejects_wrong_signature():
    """Token with wrong secret produces wrong signature."""
    token = sign_token(secret="secret1", tenant_id="tenant-123", now=1000)
    with pytest.raises(WidgetTokenError, match="signature"):
        verify_token(secret="secret2", token=token, now=1000)


def test_verify_rejects_malformed_token_no_dot():
    """Token without dot separator is rejected."""
    with pytest.raises(WidgetTokenError, match="Invalid widget token"):
        verify_token(secret="secret", token="malformed", now=1000)


def test_verify_rejects_empty_token():
    """Empty token is rejected."""
    with pytest.raises(WidgetTokenError, match="Invalid widget token"):
        verify_token(secret="secret", token="", now=1000)


def test_verify_rejects_invalid_base64_payload():
    """Token with invalid base64 payload is rejected."""
    import hashlib
    import hmac

    encoded = "!!!invalid!!!"
    signature = hmac.new(b"secret", encoded.encode("ascii"), hashlib.sha256).hexdigest()
    with pytest.raises(WidgetTokenError, match="payload"):
        verify_token(secret="secret", token=f"{encoded}.{signature}", now=1000)


def test_verify_rejects_invalid_json_payload():
    """Token with non-JSON payload is rejected."""
    import base64
    import hashlib
    import hmac

    invalid = base64.urlsafe_b64encode(b"not-json").decode("ascii").rstrip("=")
    signature = hmac.new(b"secret", invalid.encode("ascii"), hashlib.sha256).hexdigest()
    with pytest.raises(WidgetTokenError, match="payload"):
        verify_token(secret="secret", token=f"{invalid}.{signature}", now=1000)


def test_verify_rejects_missing_tenant_id():
    """Token without tenant_id field is rejected."""
    import base64
    import hashlib
    import hmac
    import json

    payload = {"iat": 1000, "exp": 2000}
    encoded = (
        base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("ascii").rstrip("=")
    )
    signature = hmac.new(b"secret", encoded.encode("ascii"), hashlib.sha256).hexdigest()
    with pytest.raises(WidgetTokenError, match="missing tenant_id"):
        verify_token(secret="secret", token=f"{encoded}.{signature}", now=1000)


def test_verify_rejects_empty_tenant_id():
    """Token with empty tenant_id string is rejected."""
    import base64
    import hashlib
    import hmac
    import json

    payload = {"tenant_id": "", "iat": 1000, "exp": 2000}
    encoded = (
        base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("ascii").rstrip("=")
    )
    signature = hmac.new(b"secret", encoded.encode("ascii"), hashlib.sha256).hexdigest()
    with pytest.raises(WidgetTokenError, match="missing tenant_id"):
        verify_token(secret="secret", token=f"{encoded}.{signature}", now=1000)


def test_verify_rejects_missing_timestamps():
    """Token without iat or exp is rejected."""
    import base64
    import hashlib
    import hmac
    import json

    payload = {"tenant_id": "tenant-123"}
    encoded = (
        base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("ascii").rstrip("=")
    )
    signature = hmac.new(b"secret", encoded.encode("ascii"), hashlib.sha256).hexdigest()
    with pytest.raises(WidgetTokenError, match="missing timestamps"):
        verify_token(secret="secret", token=f"{encoded}.{signature}", now=1000)


def test_verify_rejects_non_integer_timestamps():
    """Token with non-integer timestamps is rejected."""
    import base64
    import hashlib
    import hmac
    import json

    payload = {"tenant_id": "tenant-123", "iat": "not-int", "exp": 2000}
    encoded = (
        base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("ascii").rstrip("=")
    )
    signature = hmac.new(b"secret", encoded.encode("ascii"), hashlib.sha256).hexdigest()
    with pytest.raises(WidgetTokenError, match="missing timestamps"):
        verify_token(secret="secret", token=f"{encoded}.{signature}", now=1000)


def test_sign_uses_current_time_when_now_not_provided():
    """sign_token uses time.time() when now is not provided."""
    with mock.patch("time.time", return_value=5000.5):
        token = sign_token(secret="secret", tenant_id="tenant-123")
        result = verify_token(secret="secret", token=token, now=5000)
        assert result.iat == 5000
        assert result.exp == 5000 + 3600


def test_verify_uses_current_time_when_now_not_provided():
    """verify_token uses time.time() when now is not provided."""
    token = sign_token(secret="secret", tenant_id="tenant-123", now=1000, ttl_seconds=100)
    with mock.patch("time.time", return_value=1050.5):
        result = verify_token(secret="secret", token=token)
        assert result.tenant_id == "tenant-123"


def test_sign_token_default_ttl():
    """sign_token defaults to 3600 seconds TTL."""
    token = sign_token(secret="secret", tenant_id="tenant-123", now=1000)
    result = verify_token(secret="secret", token=token, now=1000)
    assert result.exp == 1000 + 3600


def test_sign_token_custom_ttl():
    """sign_token accepts custom ttl_seconds."""
    token = sign_token(secret="secret", tenant_id="tenant-123", ttl_seconds=7200, now=1000)
    result = verify_token(secret="secret", token=token, now=1000)
    assert result.exp == 1000 + 7200
