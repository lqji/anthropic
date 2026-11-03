from datetime import date, datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from clinic.api.deps import get_deps
from clinic.deps import Deps
from clinic.services.appointments import AppointmentService

router = APIRouter(prefix="/api/v1", tags=["appointments"])


class SpecialtyOut(BaseModel):
    id: int
    name: str


class DoctorOut(BaseModel):
    id: int
    name: str
    specialty: str


class SlotOut(BaseModel):
    id: int
    doctor_id: int
    doctor: str
    specialty: str
    starts_at: datetime  # in clinic time, with its UTC offset
    ends_at: datetime


def get_service(deps: Annotated[Deps, Depends(get_deps)]) -> AppointmentService:
    return AppointmentService(deps.pool, deps.clock, deps.settings.clinic_timezone)


Service = Annotated[AppointmentService, Depends(get_service)]


@router.get("/specialties")
def list_specialties(service: Service) -> list[SpecialtyOut]:
    return [SpecialtyOut(id=row.id, name=row.name) for row in service.specialties()]


@router.get("/doctors")
def list_doctors(service: Service, specialty: str | None = None) -> list[DoctorOut]:
    """All doctors, or the doctors of one specialty (case-insensitive name)."""
    return [
        DoctorOut(id=row.id, name=row.name, specialty=row.specialty)
        for row in service.doctors(specialty)
    ]


@router.get("/slots")
def list_free_slots(
    deps: Annotated[Deps, Depends(get_deps)],
    service: Service,
    date: Annotated[date, Query(description="Day in clinic time, YYYY-MM-DD; not in the past")],
    doctor_id: int | None = None,
    specialty: str | None = None,
) -> list[SlotOut]:
    """Free slots that have not started yet, by time. Needs `doctor_id` or `specialty`."""
    zone = ZoneInfo(deps.settings.clinic_timezone)
    return [
        SlotOut(
            id=row.id,
            doctor_id=row.doctor_id,
            doctor=row.doctor,
            specialty=row.specialty,
            starts_at=row.starts_at.astimezone(zone),
            ends_at=row.ends_at.astimezone(zone),
        )
        for row in service.free_slots(day=date, doctor_id=doctor_id, specialty=specialty)
    ]
