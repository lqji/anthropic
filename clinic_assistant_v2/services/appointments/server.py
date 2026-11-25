"""Appointments FastMCP Server (Port 8001).

Manages clinic appointment bookings. Tools take no user ID argument;
they derive patient identity strictly from the verified sub claim in the Bearer JWT.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

from fastmcp import FastMCP

from shared.auth import verify_token

mcp = FastMCP("Appointments MCP")

# Seeded appointments for testing cross-user isolation
SEED_APPOINTMENTS: dict[str, list[dict[str, Any]]] = {
    "user-a": [
        {
            "id": "apt-a1",
            "doctor": "Dr. Sarah Smith",
            "specialty": "General Medicine",
            "date": "2026-12-01",
            "time": "09:30",
            "notes": "Follow-up consultation",
            "status": "confirmed",
        }
    ],
    "user-b": [
        {
            "id": "apt-b1",
            "doctor": "Dr. John Doe",
            "specialty": "Cardiology",
            "date": "2026-12-03",
            "time": "14:00",
            "notes": "Blood pressure check",
            "status": "confirmed",
        }
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
def list_my_appointments(token: str | None = None) -> list[dict[str, Any]] | dict[str, str]:
    """List all appointments for the authenticated patient.

    Identity is derived strictly from the verified JWT sub claim.
    """
    auth_header = _get_auth_header(token)
    claims = verify_token(auth_header, required_scope="appointments:read")
    if "error" in claims:
        return {"error": "unauthorized"}

    sub = claims["sub"]
    return SEED_APPOINTMENTS.get(sub, [])


@mcp.tool()
def book_appointment(
    doctor: str,
    date: str,
    time: str,
    notes: str = "",
    token: str | None = None,
) -> dict[str, Any]:
    """Book a new appointment for the authenticated patient.

    Identity is derived strictly from the verified JWT sub claim.
    """
    auth_header = _get_auth_header(token)
    claims = verify_token(auth_header, required_scope="appointments:write")
    if "error" in claims:
        return {"error": "unauthorized"}

    sub = claims["sub"]
    new_apt = {
        "id": f"apt-{uuid.uuid4().hex[:6]}",
        "doctor": doctor,
        "date": date,
        "time": time,
        "notes": notes,
        "status": "confirmed",
    }
    if sub not in SEED_APPOINTMENTS:
        SEED_APPOINTMENTS[sub] = []
    SEED_APPOINTMENTS[sub].append(new_apt)
    return {"status": "success", "appointment": new_apt}


@mcp.tool()
def cancel_appointment(
    appointment_id: str,
    token: str | None = None,
) -> dict[str, Any]:
    """Cancel an existing appointment belonging to the authenticated patient."""
    auth_header = _get_auth_header(token)
    claims = verify_token(auth_header, required_scope="appointments:write")
    if "error" in claims:
        return {"error": "unauthorized"}

    sub = claims["sub"]
    user_apts = SEED_APPOINTMENTS.get(sub, [])
    for apt in user_apts:
        if apt["id"] == appointment_id:
            apt["status"] = "cancelled"
            return {"status": "success", "message": f"Appointment {appointment_id} cancelled"}

    return {"error": "Appointment not found"}


if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8001"))
    mcp.run(transport="http", host=host, port=port)
