"""Medical Records FastMCP Server (Port 8003).

Provides patient-isolated medical records. Tools take no user ID argument;
they derive customer identity strictly from the verified sub in the Bearer JWT.
"""

from __future__ import annotations

import os
from typing import Any

from fastmcp import FastMCP

from shared.auth import verify_token

mcp = FastMCP("Medical Records MCP")

# Seeded records for testing cross-user isolation
SEED_RECORDS: dict[str, list[dict[str, Any]]] = {
    "user-a": [
        {"date": "2026-09-10", "diagnosis": "Annual Checkup", "notes": "All vitals normal"},
        {"date": "2026-10-02", "diagnosis": "Flu", "notes": "Rest and hydration prescribed"},
    ],
    "user-b": [
        {"date": "2026-08-14", "diagnosis": "Sprained Ankle", "notes": "Bandage and cold compress"},
        {
            "date": "2026-11-15",
            "diagnosis": "Hypertension",
            "notes": "Prescribed Lisinopril 10mg daily",
        },
    ],
}


def _get_auth_header(token: str | None = None) -> str | None:
    if token:
        return token
    try:
        from fastmcp.server.dependencies import get_http_headers

        headers = get_http_headers()
        return headers.get("authorization")
    except Exception:
        return None


@mcp.tool()
def get_my_medical_records(token: str | None = None) -> list[dict[str, Any]] | dict[str, str]:
    """Retrieve all medical records belonging to the authenticated patient.

    Identity is derived strictly from the verified JWT sub claim.
    """
    auth_header = _get_auth_header(token)
    claims = verify_token(auth_header, required_scope="records:read")
    if "error" in claims:
        return {"error": "unauthorized"}

    sub = claims["sub"]
    records = SEED_RECORDS.get(sub, [])
    return records


@mcp.tool()
def get_my_last_medical_record(token: str | None = None) -> dict[str, Any]:
    """Retrieve the most recent medical record for the authenticated patient.

    Identity is derived strictly from the verified JWT sub claim.
    """
    auth_header = _get_auth_header(token)
    claims = verify_token(auth_header, required_scope="records:read")
    if "error" in claims:
        return {"error": "unauthorized"}

    sub = claims["sub"]
    records = SEED_RECORDS.get(sub, [])
    if not records:
        return {"message": "No medical records found"}
    return records[-1]


if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8003"))
    mcp.run(transport="http", host=host, port=port)
