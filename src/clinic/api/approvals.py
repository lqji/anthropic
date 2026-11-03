from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, StringConstraints

from clinic.api.deps import get_deps
from clinic.deps import Deps
from clinic.services.approval import ApprovalService, DecisionResult

router = APIRouter(prefix="/api/v1/approvals", tags=["approvals"])

DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500

DecidedBy = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]


class Decision(BaseModel):
    decided_by: DecidedBy
    reason: Reason


class ApprovalOut(BaseModel):
    id: UUID
    conversation_id: UUID
    kind: str
    booking_id: int
    status: str
    decided_by: str | None
    reason: str | None
    created_at: datetime
    decided_at: datetime | None


class DecisionOut(BaseModel):
    approval_id: UUID
    status: str
    patient_message: str


def get_service(deps: Annotated[Deps, Depends(get_deps)]) -> ApprovalService:
    return ApprovalService(deps.pool, deps.clock, deps.locks, deps.approval_resumer)


Service = Annotated[ApprovalService, Depends(get_service)]


@router.get("")
def list_approvals(
    service: Service,
    status: Literal["pending", "approved", "rejected"] = "pending",
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ApprovalOut]:
    """Approvals for front-desk staff, oldest first, one page at a time."""
    return [
        ApprovalOut(
            id=row.id,
            conversation_id=row.conversation_id,
            kind=row.kind,
            booking_id=row.booking_id,
            status=row.status,
            decided_by=row.decided_by,
            reason=row.reason,
            created_at=row.created_at,
            decided_at=row.decided_at,
        )
        for row in service.list_by_status(status, limit, offset)
    ]


@router.post("/{approval_id}/approve")
def approve(approval_id: str, body: Decision, service: Service) -> DecisionOut:
    return _out(service.decide(approval_id, "approved", body.decided_by, body.reason))


@router.post("/{approval_id}/reject")
def reject(approval_id: str, body: Decision, service: Service) -> DecisionOut:
    return _out(service.decide(approval_id, "rejected", body.decided_by, body.reason))


def _out(result: DecisionResult) -> DecisionOut:
    return DecisionOut(
        approval_id=result.approval_id,
        status=result.status,
        patient_message=result.patient_message,
    )
