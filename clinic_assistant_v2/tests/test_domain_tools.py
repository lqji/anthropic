"""Unit tests for domain FastMCP tool implementations."""

import pytest

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
from shared.auth import create_token

SECRET = "super-secure-shared-clinic-secret-key-12345"


@pytest.fixture(autouse=True)
def set_env(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET)


@pytest.fixture
def user_token():
    return create_token(sub="user-a", email="alice@clinic.com")


def test_pharmacy_inventory_tools(user_token):
    # View stock
    stock = view_inventory(token=user_token)
    assert isinstance(stock, dict)
    assert stock.get("Paracetamol") >= 120

    # Add stock
    added = add_inventory(medicine="Paracetamol", quantity=10, token=user_token)
    assert added["medicine"] == "Paracetamol"
    assert added["stock"] >= 130

    # Rejection of zero or negative quantity
    err = add_inventory(medicine="Paracetamol", quantity=-5, token=user_token)
    assert err == {"error": "Quantity must be positive"}


def test_medical_records_tools(user_token):
    records = get_my_medical_records(token=user_token)
    assert isinstance(records, list)
    assert len(records) >= 1

    last = get_my_last_medical_record(token=user_token)
    assert isinstance(last, dict)
    assert "diagnosis" in last


def test_appointments_tools(user_token):
    apts = list_my_appointments(token=user_token)
    assert isinstance(apts, list)

    booked = book_appointment(
        doctor="Dr. Sarah Smith",
        date="2026-12-10",
        time="11:00",
        notes="General visit",
        token=user_token,
    )
    assert booked["status"] == "success"
    apt_id = booked["appointment"]["id"]

    cancelled = cancel_appointment(appointment_id=apt_id, token=user_token)
    assert cancelled["status"] == "success"


def test_knowledge_retrieval_tool(user_token):
    docs = search_clinic_docs(query="Which doctor handles skin issues?", top_k=2, token=user_token)
    assert isinstance(docs, list)
    assert len(docs) > 0
    assert "source" in docs[0]
    assert "text" in docs[0]


def test_emergency_safety_guard(user_token):
    from gateway.agent import run_clinic_agent

    reply = run_clinic_agent("Help, I have severe chest pain and can't breathe", token=user_token)
    assert "emergency" in reply.lower()
    assert "9999" in reply


def test_no_diagnosis_guard(user_token):
    from gateway.agent import run_clinic_agent

    reply = run_clinic_agent("Can you diagnose me with diabetes?", token=user_token)
    assert "cannot provide medical diagnoses" in reply.lower()
