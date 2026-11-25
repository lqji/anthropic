"""Tests for the shared JWT authentication and verification module."""

import time

import pytest

from shared.auth import create_token, verify_token

SECRET = "super-secure-shared-clinic-secret-key-12345"


@pytest.fixture(autouse=True)
def set_env_secret(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET)


def test_create_and_verify_valid_token():
    token = create_token(sub="user-123", email="user@clinic.com", scopes=["appointments:read"])
    claims = verify_token(token, required_scope="appointments:read")

    assert claims.get("sub") == "user-123"
    assert claims.get("email") == "user@clinic.com"
    assert "appointments:read" in claims.get("scopes", [])
    assert claims.get("exp") > int(time.time())


def test_verify_with_bearer_header_format():
    token = create_token(sub="user-456", email="patient@clinic.com")
    header = f"Bearer {token}"
    claims = verify_token(header, required_scope="records:read")
    assert claims.get("sub") == "user-456"


def test_verify_missing_token():
    assert verify_token(None) == {"error": "unauthorized"}
    assert verify_token("") == {"error": "unauthorized"}
    assert verify_token("Bearer ") == {"error": "unauthorized"}


def test_verify_expired_token():
    token = create_token(sub="user-123", email="user@clinic.com", exp_seconds=-10)
    assert verify_token(token) == {"error": "unauthorized"}


def test_verify_wrong_signature():
    token = create_token(
        sub="user-123",
        email="user@clinic.com",
        secret="different-wrong-secret-key-32-bytes-minimum!!",
    )
    assert verify_token(token) == {"error": "unauthorized"}


def test_verify_missing_scope():
    token = create_token(sub="user-123", email="user@clinic.com", scopes=["records:read"])
    # Token has records:read but we require appointments:write
    assert verify_token(token, required_scope="appointments:write") == {"error": "unauthorized"}
