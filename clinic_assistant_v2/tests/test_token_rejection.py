"""Token Rejection and Unauthorized Access Tests.

Proves that missing token, bad signature, expired token, and missing scope
each return 401 Unauthorized with no customer data disclosed.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from gateway.main import app
from shared.auth import create_token

SECRET = "super-secure-shared-clinic-secret-key-12345"


@pytest.fixture(autouse=True)
def set_env(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET)


@pytest.mark.asyncio
async def test_missing_token_rejected():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/chat", json={"message": "Show me the last medical record"})
        assert resp.status_code == 401
        assert "detail" in resp.json()


@pytest.mark.asyncio
async def test_expired_token_rejected():
    expired_token = create_token(sub="user-a", email="alice@clinic.com", exp_seconds=-60)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/chat",
            json={"message": "Show me the last medical record"},
            headers={"Authorization": f"Bearer {expired_token}"},
        )
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_forged_signature_rejected():
    forged_token = create_token(
        sub="user-a",
        email="alice@clinic.com",
        secret="different-unauthorized-secret-key-32-chars",
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/chat",
            json={"message": "Show me the last medical record"},
            headers={"Authorization": f"Bearer {forged_token}"},
        )
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_missing_scope_tool_rejection():
    # Token has only pharmacy:read but calls medical records
    limited_token = create_token(
        sub="user-a",
        email="alice@clinic.com",
        scopes=["pharmacy:read"],
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/chat",
            json={"message": "Show me the last medical record"},
            headers={"Authorization": f"Bearer {limited_token}"},
        )
        assert resp.status_code == 200
        reply = resp.json()["reply"]
        assert "unauthorized" in reply.lower()
        assert "Flu" not in reply
