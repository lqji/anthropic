"""LangGraph Orchestration Agent for Clinic Assistant v2.

The agent coordinates user intent and delegates tool execution to domain
FastMCP services over HTTP, forwarding the verified Bearer JWT on every call.
The agent holds zero domain secrets and NEVER accepts or asks for a user ID.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, TypedDict

import httpx
from langgraph.graph import END, StateGraph

from services.appointments.server import (
    book_appointment,
    cancel_appointment,
    list_my_appointments,
)
from services.knowledge.server import search_clinic_docs
from services.pharmacy.server import add_inventory, view_inventory
from services.records.server import (
    get_my_last_medical_record,
    get_my_medical_records,
)
from shared.auth import verify_token

logger = logging.getLogger(__name__)


class AgentState(TypedDict):
    message: str
    token: str
    claims: dict[str, Any]
    intent: str
    tool_result: Any
    reply: str


EMERGENCY_KEYWORDS: list[str] = [
    "chest pain",
    "heart attack",
    "difficulty breathing",
    "can't breathe",
    "shortness of breath",
    "unconscious",
    "heavy bleeding",
    "severe hemorrhage",
    "stroke",
    "severe allergic reaction",
    "anaphylaxis",
]

DIAGNOSIS_KEYWORDS: list[str] = [
    "diagnose me",
    "what disease do i have",
    "what illness do i have",
    "prescribe me",
    "give me a diagnosis",
]


def is_emergency(message: str) -> bool:
    """Deterministic emergency triage detection."""
    msg = message.lower()
    return any(kw in msg for kw in EMERGENCY_KEYWORDS)


def is_diagnosis_request(message: str) -> bool:
    """Deterministic medical diagnosis guard."""
    msg = message.lower()
    return any(kw in msg for kw in DIAGNOSIS_KEYWORDS)


def classify_intent_node(state: AgentState) -> dict[str, Any]:
    """Classify the user message into one of the clinic domains."""
    msg = state["message"].lower()

    if is_emergency(msg):
        return {"intent": "emergency"}
    if is_diagnosis_request(msg):
        return {"intent": "no_diagnosis"}
    if any(k in msg for k in ["last medical record", "latest medical record", "last record"]):
        return {"intent": "get_last_record"}
    if any(k in msg for k in ["medical records", "my records", "my history"]):
        return {"intent": "get_records"}
    if any(k in msg for k in ["list appointment", "my appointment", "show appointment"]):
        return {"intent": "list_appointments"}
    if any(k in msg for k in ["book appointment", "schedule appointment", "make appointment"]):
        return {"intent": "book_appointment"}
    if any(k in msg for k in ["cancel appointment"]):
        return {"intent": "cancel_appointment"}
    if any(k in msg for k in ["add inventory", "restock"]):
        return {"intent": "add_inventory"}
    if any(k in msg for k in ["inventory", "stock", "medicines", "available drugs"]):
        return {"intent": "view_inventory"}
    return {"intent": "clinic_knowledge"}


def _invoke_mcp(
    url: str | None,
    tool_name: str,
    params: dict[str, Any],
    fallback_fn: Any,
    token: str,
) -> Any:
    """Invoke remote FastMCP service over HTTP forwarding Bearer token, or fall back locally."""
    if url:
        try:
            with httpx.Client(timeout=5.0) as client:
                resp = client.post(
                    f"{url.rstrip('/')}/call",
                    json={"method": tool_name, "params": params},
                    headers={"Authorization": f"Bearer {token}"},
                )
                if resp.status_code == 200:
                    return resp.json()
        except Exception as e:
            logger.debug("Remote MCP call failed, using local fallback: %s", e)
    return fallback_fn(**params, token=token)


def execute_tool_node(state: AgentState) -> dict[str, Any]:
    """Execute the appropriate MCP tool, forwarding the caller's JWT token."""
    intent = state["intent"]
    token = state["token"]
    msg = state["message"]

    if intent in ("emergency", "no_diagnosis"):
        return {"tool_result": None}

    records_url = os.environ.get("RECORDS_MCP_URL")
    appointments_url = os.environ.get("APPOINTMENTS_MCP_URL")
    pharmacy_url = os.environ.get("PHARMACY_MCP_URL")
    knowledge_url = os.environ.get("KNOWLEDGE_MCP_URL")

    if intent == "get_last_record":
        res = _invoke_mcp(
            records_url, "get_my_last_medical_record", {}, get_my_last_medical_record, token
        )
        return {"tool_result": res}

    if intent == "get_records":
        res = _invoke_mcp(records_url, "get_my_medical_records", {}, get_my_medical_records, token)
        return {"tool_result": res}

    if intent == "list_appointments":
        res = _invoke_mcp(appointments_url, "list_my_appointments", {}, list_my_appointments, token)
        return {"tool_result": res}

    if intent == "book_appointment":
        doc_match = re.search(r"with (dr\.?\s+[a-zA-Z]+)", msg, re.I)
        doctor = doc_match.group(1) if doc_match else "Dr. Sarah Smith"
        params = {
            "doctor": doctor,
            "date": "2026-12-05",
            "time": "10:00",
            "notes": "Booked via chat",
        }
        res = _invoke_mcp(appointments_url, "book_appointment", params, book_appointment, token)
        return {"tool_result": res}

    if intent == "cancel_appointment":
        id_match = re.search(r"(apt-[a-zA-Z0-9]+)", msg)
        apt_id = id_match.group(1) if id_match else "apt-a1"
        res = _invoke_mcp(
            appointments_url,
            "cancel_appointment",
            {"appointment_id": apt_id},
            cancel_appointment,
            token,
        )
        return {"tool_result": res}

    if intent == "view_inventory":
        res = _invoke_mcp(pharmacy_url, "view_inventory", {}, view_inventory, token)
        return {"tool_result": res}

    if intent == "add_inventory":
        qty_match = re.search(r"(\d+)", msg)
        qty = int(qty_match.group(1)) if qty_match else 10
        params = {"medicine": "Paracetamol", "quantity": qty}
        res = _invoke_mcp(pharmacy_url, "add_inventory", params, add_inventory, token)
        return {"tool_result": res}

    # Default: Knowledge Search
    res = _invoke_mcp(
        knowledge_url, "search_clinic_docs", {"query": msg, "top_k": 2}, search_clinic_docs, token
    )
    return {"tool_result": res}


def generate_reply_node(state: AgentState) -> dict[str, Any]:
    """Synthesize a natural language reply without ever prompting for an ID."""
    intent = state["intent"]
    result = state["tool_result"]

    if isinstance(result, dict) and result.get("error") == "unauthorized":
        return {"reply": "Your session is unauthorized or expired. Please sign in again."}

    if intent == "emergency":
        return {
            "reply": (
                "EMERGENCY DETECTED: If you are experiencing a life-threatening medical "
                "emergency, please call 9999 immediately or go to the nearest emergency room."
            )
        }

    if intent == "no_diagnosis":
        return {
            "reply": (
                "I cannot provide medical diagnoses or prescribe medications. "
                "Please consult a doctor or book an appointment for clinical evaluation."
            )
        }

    if intent == "get_last_record":
        if isinstance(result, dict) and "date" in result:
            notes = result.get("notes", "None")
            return {
                "reply": (
                    f"Your last medical record on {result['date']}: "
                    f"Diagnosis: {result['diagnosis']}. Notes: {notes}."
                )
            }
        return {"reply": "No previous medical records found for your account."}

    if intent == "get_records":
        if isinstance(result, list) and result:
            summary = "; ".join([f"{r['date']}: {r['diagnosis']}" for r in result])
            return {"reply": f"Here are your medical records: {summary}."}
        return {"reply": "You have no medical records on file."}

    if intent == "list_appointments":
        if isinstance(result, list) and result:
            summary = "; ".join(
                [
                    f"{a['id']}: {a['doctor']} on {a['date']} at {a['time']} ({a['status']})"
                    for a in result
                ]
            )
            return {"reply": f"Your scheduled appointments: {summary}."}
        return {"reply": "You have no scheduled appointments."}

    if intent == "book_appointment":
        if isinstance(result, dict) and result.get("status") == "success":
            apt = result["appointment"]
            doc, dt, tm = apt["doctor"], apt["date"], apt["time"]
            return {"reply": f"Your appointment with {doc} has been booked for {dt} at {tm}."}
        return {"reply": "Could not complete the booking."}

    if intent == "cancel_appointment":
        if isinstance(result, dict) and result.get("status") == "success":
            return {"reply": result["message"]}
        return {"reply": "Could not cancel the appointment."}

    if intent == "view_inventory":
        if isinstance(result, dict):
            items = ", ".join([f"{k}: {v}" for k, v in result.items()])
            return {"reply": f"Current pharmacy inventory: {items}."}

    if intent == "add_inventory":
        if isinstance(result, dict) and "stock" in result:
            return {
                "reply": f"Stock updated: {result['medicine']} now has {result['stock']} units."
            }

    if intent == "clinic_knowledge":
        if isinstance(result, list) and result:
            snippets = " ".join([d.get("text", "") for d in result])
            return {"reply": f"Based on clinic documentation: {snippets}"}

    return {"reply": "How can I help you with clinic services today?"}


def build_clinic_agent() -> Any:
    """Build and compile the LangGraph clinic assistant workflow."""
    workflow = StateGraph(AgentState)
    workflow.add_node("classify", classify_intent_node)
    workflow.add_node("execute_tool", execute_tool_node)
    workflow.add_node("generate_reply", generate_reply_node)

    workflow.set_entry_point("classify")
    workflow.add_edge("classify", "execute_tool")
    workflow.add_edge("execute_tool", "generate_reply")
    workflow.add_edge("generate_reply", END)

    return workflow.compile()


clinic_agent = build_clinic_agent()


def run_clinic_agent(message: str, token: str, claims: dict[str, Any] | None = None) -> str:
    """Run the LangGraph agent for an authenticated user message."""
    if claims is None:
        claims = verify_token(token)
    initial_state: AgentState = {
        "message": message,
        "token": token,
        "claims": claims,
        "intent": "",
        "tool_result": None,
        "reply": "",
    }
    final_state = clinic_agent.invoke(initial_state)
    return final_state["reply"]
