"""Pharmacy Inventory FastMCP Server (Port 8002).

Manages clinic medicine stock and inventory.
"""

from __future__ import annotations

import os
from typing import Any

from fastmcp import FastMCP

from shared.auth import verify_token

mcp = FastMCP("Pharmacy Inventory MCP")

# Default seeded stock
INVENTORY: dict[str, int] = {
    "Paracetamol": 120,
    "Ibuprofen": 40,
    "Cetirizine": 25,
    "Amoxicillin": 50,
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
def view_inventory(token: str | None = None) -> dict[str, Any]:
    """Return the current clinic medicine stock."""
    auth_header = _get_auth_header(token)
    claims = verify_token(auth_header, required_scope="pharmacy:read")
    if "error" in claims:
        return {"error": "unauthorized"}

    return dict(INVENTORY)


@mcp.tool()
def add_inventory(
    medicine: str,
    quantity: int,
    token: str | None = None,
) -> dict[str, Any]:
    """Add new stock for a medicine. Quantity must be strictly positive."""
    auth_header = _get_auth_header(token)
    claims = verify_token(auth_header, required_scope="pharmacy:write")
    if "error" in claims:
        return {"error": "unauthorized"}

    if quantity <= 0:
        return {"error": "Quantity must be positive"}

    med_name = medicine.strip().title()
    INVENTORY[med_name] = INVENTORY.get(med_name, 0) + quantity
    return {"medicine": med_name, "stock": INVENTORY[med_name]}


if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8002"))
    mcp.run(transport="http", host=host, port=port)
