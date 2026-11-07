from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from clinic.api.deps import get_deps
from clinic.deps import Deps
from clinic.services.audit import AuditService

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])

DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500


class AuditEventOut(BaseModel):
    id: int
    at: datetime
    actor_type: str
    actor: str
    action: str
    outcome: str
    booking_id: int
    conversation_id: UUID
    reason: str | None


def get_service(deps: Annotated[Deps, Depends(get_deps)]) -> AuditService:
    return AuditService(deps.pool)


@router.get("")
def list_audit(
    service: Annotated[AuditService, Depends(get_service)],
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
    booking_id: int | None = None,
    conversation_id: UUID | None = None,
) -> list[AuditEventOut]:
    """What was booked, cancelled, approved or rejected, newest first, one page at a time."""
    return [
        AuditEventOut(
            id=event.id,
            at=event.at,
            actor_type=event.actor_type,
            actor=event.actor,
            action=event.action,
            outcome=event.outcome,
            booking_id=event.booking_id,
            conversation_id=event.conversation_id,
            reason=event.reason,
        )
        for event in service.list_events(limit, offset, booking_id, conversation_id)
    ]
