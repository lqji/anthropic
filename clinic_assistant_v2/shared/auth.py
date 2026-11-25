"""JWT Authentication and Verification Helper.

Shared contract used across FastAPI and all domain FastMCP servers.
"""

from __future__ import annotations

import os
import time
from typing import Any

import jwt

JWT_ALGORITHM = "HS256"
DEFAULT_TOKEN_EXPIRY = 3600  # 1 hour in seconds

STANDARD_SCOPES = [
    "appointments:read",
    "appointments:write",
    "records:read",
    "pharmacy:read",
    "pharmacy:write",
    "docs:read",
]


def get_jwt_secret() -> str:
    """Retrieve the shared JWT secret from the environment."""
    secret = os.environ.get("JWT_SECRET")
    if not secret:
        raise ValueError("JWT_SECRET environment variable is not configured")
    return secret


def create_token(
    sub: str,
    email: str,
    scopes: list[str] | None = None,
    exp_seconds: int = DEFAULT_TOKEN_EXPIRY,
    secret: str | None = None,
) -> str:
    """Mint a signed HS256 JWT containing sub, email, scopes, and exp."""
    signing_secret = secret or get_jwt_secret()
    now = int(time.time())
    payload = {
        "sub": sub,
        "email": email,
        "scopes": scopes if scopes is not None else STANDARD_SCOPES,
        "exp": now + exp_seconds,
    }
    return jwt.encode(payload, signing_secret, algorithm=JWT_ALGORITHM)


def extract_bearer_token(auth_header: str | None) -> str | None:
    """Extract raw JWT string from an 'Authorization: Bearer <token>' header."""
    if not auth_header:
        return None
    parts = auth_header.strip().split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None


def verify_token(
    token_or_header: str | None,
    required_scope: str | None = None,
    secret: str | None = None,
) -> dict[str, Any]:
    """Verify an HS256 JWT, check expiration, signature, and required scope.

    Accepts either a raw token or an Authorization header string.
    Returns the decoded claims dictionary on success.
    Returns {"error": "unauthorized"} on failure.
    """
    if not token_or_header:
        return {"error": "unauthorized"}

    # Handle Authorization: Bearer <token> or raw token string
    if token_or_header.strip().lower().startswith("bearer "):
        token = extract_bearer_token(token_or_header)
    else:
        token = token_or_header.strip()

    if not token:
        return {"error": "unauthorized"}

    try:
        verify_secret = secret or get_jwt_secret()
        payload = jwt.decode(token, verify_secret, algorithms=[JWT_ALGORITHM])

        # Validate standard claims presence
        if "sub" not in payload or "email" not in payload or "scopes" not in payload:
            return {"error": "unauthorized"}

        # Validate required scope if specified
        if required_scope:
            scopes = payload.get("scopes", [])
            if not isinstance(scopes, list) or required_scope not in scopes:
                return {"error": "unauthorized"}

        return payload

    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError, ValueError):
        return {"error": "unauthorized"}
