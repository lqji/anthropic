"""Identification Without Asking Tests.

Proves that when signed in as User A, requesting 'Show me the last medical record'
returns User A's last record immediately, and that the bot never asks
'What is your ID?' or requests a patient number.
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
async def test_bot_never_asks_for_id_when_fetching_record():
    token = create_token(sub="user-a", email="alice@clinic.com")
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/chat",
            json={"message": "Show me the last medical record"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        reply = resp.json()["reply"]

        # Proves User A's record was fetched
        assert "Flu" in reply

        # Proves the bot NEVER asks for an ID or patient number
        lower_reply = reply.lower()
        forbidden_phrases = [
            "what's your id",
            "what is your id",
            "patient id",
            "patient number",
            "provide your id",
            "tell me your id",
            "who are you",
        ]
        for phrase in forbidden_phrases:
            assert phrase not in lower_reply, f"Bot asked for ID: '{phrase}' found in '{reply}'"
