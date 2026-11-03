from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from clinic.api.deps import get_deps
from clinic.deps import Deps
from clinic.services.emergency import EmergencyService

router = APIRouter(prefix="/api/v1/emergencies", tags=["emergencies"])


class EmergencyOut(BaseModel):
    id: int
    conversation_id: UUID
    matched_rule: str
    excerpt: str
    status: str
    created_at: datetime


def get_service(deps: Annotated[Deps, Depends(get_deps)]) -> EmergencyService:
    return EmergencyService(deps.pool, deps.clock, deps.settings.emergency_number)


@router.get("")
def list_emergencies(
    service: Annotated[EmergencyService, Depends(get_service)],
    status: Literal["open", "acknowledged"] = "open",
) -> list[EmergencyOut]:
    """Emergency flags for front-desk staff, newest first."""
    return [
        EmergencyOut(
            id=row.id,
            conversation_id=row.conversation_id,
            matched_rule=row.matched_rule,
            excerpt=row.excerpt,
            status=row.status,
            created_at=row.created_at,
        )
        for row in service.list_by_status(status)
    ]
