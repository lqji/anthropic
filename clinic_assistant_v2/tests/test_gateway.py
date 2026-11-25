"""FastAPI Gateway Endpoint Tests."""

import pytest
from httpx import ASGITransport, AsyncClient

from gateway.main import app
from shared.auth import verify_token

SECRET = "super-secure-shared-clinic-secret-key-12345"


@pytest.fixture(autouse=True)
def set_env(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "clinic-test-client-id")
    monkeypatch.setenv("TEST_AUTH_ENABLED", "true")


@pytest.mark.asyncio
async def test_health():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_auth_login_redirect():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/auth/login", follow_redirects=False)
        assert resp.status_code == 307
        loc = resp.headers["location"]
        assert "accounts.google.com/o/oauth2/v2/auth" in loc
        assert "client_id=clinic-test-client-id" in loc
        assert "response_type=code" in loc


@pytest.mark.asyncio
async def test_auth_callback_mints_jwt():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/auth/callback?code=test-code-alice-123")
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["sub"] == "google-sub-alice-123"

        # Verify minted token contains expected claims
        claims = verify_token(data["access_token"])
        assert claims["sub"] == "google-sub-alice-123"
        assert "records:read" in claims["scopes"]
        assert "appointments:read" in claims["scopes"]


@pytest.mark.asyncio
async def test_auth_callback_rejects_test_code_when_disabled(monkeypatch):
    monkeypatch.setenv("TEST_AUTH_ENABLED", "false")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/auth/callback?code=test-code-alice-123")
        assert resp.status_code == 403
        assert "disabled" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_auth_callback_fails_when_unconfigured_in_prod(monkeypatch):
    monkeypatch.setenv("TEST_AUTH_ENABLED", "false")
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/auth/callback?code=regular-google-code")
        assert resp.status_code == 500
        assert "not configured" in resp.json()["detail"].lower()
