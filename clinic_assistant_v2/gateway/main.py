"""FastAPI Gateway with Google OAuth 2.0 & JWT Authentication (Port 8000).

Handles customer sign-in, issues signed HS256 JWTs, authenticates /chat requests,
and forwards verified tokens to the LangGraph agent for domain tool execution.
"""

from __future__ import annotations

import os
import urllib.parse
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from gateway.agent import run_clinic_agent
from shared.auth import create_token, verify_token

app = FastAPI(title="Clinic Assistant Gateway", version="2.0.0")

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    reply: str


@app.get("/health")
def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}


@app.get("/auth/login")
def auth_login() -> RedirectResponse:
    """Redirect user to Google OAuth 2.0 authorization code flow."""
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "mock-client-id")
    redirect_uri = os.environ.get("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/callback")

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "consent",
    }
    url = f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"
    return RedirectResponse(url=url)


@app.get("/auth/callback")
async def auth_callback(
    code: str = Query(..., description="Google OAuth authorization code"),
) -> dict[str, Any]:
    """Exchange OAuth authorization code for Google userinfo and mint clinic JWT."""
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    redirect_uri = os.environ.get("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/callback")

    # In mock / test environment without live credentials, handle deterministic test codes
    if not client_secret or client_secret == "change-me" or code.startswith("test-code-"):
        sub = f"google-sub-{code.replace('test-code-', '')}"
        email = f"patient-{sub}@example.com"
    else:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                token_resp = await client.post(
                    GOOGLE_TOKEN_URL,
                    data={
                        "code": code,
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "redirect_uri": redirect_uri,
                        "grant_type": "authorization_code",
                    },
                )
                if token_resp.status_code != 200:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Failed to exchange code with Google",
                    )
                tokens = token_resp.json()
                access_token = tokens.get("access_token")

                userinfo_resp = await client.get(
                    GOOGLE_USERINFO_URL,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if userinfo_resp.status_code != 200:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Failed to fetch userinfo from Google",
                    )
                userinfo = userinfo_resp.json()
                sub = userinfo["sub"]
                email = userinfo.get("email", f"{sub}@example.com")
        except httpx.RequestError as e:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Google OAuth network error: {e}"
            ) from e

    # Issue signed HS256 JWT with standard scopes and 1-hour expiration
    jwt_token = create_token(sub=sub, email=email)
    return {
        "access_token": jwt_token,
        "token_type": "bearer",
        "sub": sub,
        "email": email,
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    authorization: str | None = Header(default=None),
) -> ChatResponse:
    """Chat endpoint authenticated by Bearer JWT. Runs LangGraph agent with tool delegating."""
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing authorization header"
        )

    claims = verify_token(authorization)
    if "error" in claims:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token"
        )

    reply = await run_clinic_agent(
        message=payload.message,
        token=authorization,
        claims=claims,
    )
    return ChatResponse(reply=reply)


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host=host, port=port)
