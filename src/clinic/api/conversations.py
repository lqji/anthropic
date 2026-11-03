from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, StringConstraints

from clinic.api.deps import get_deps
from clinic.deps import Deps
from clinic.services.conversation import ConversationService

router = APIRouter(prefix="/api/v1/conversations", tags=["conversations"])

MAX_MESSAGE_CHARS = 2000

PatientRef = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
MessageText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_MESSAGE_CHARS)
]


class StartConversation(BaseModel):
    patient_ref: PatientRef


class SendMessage(BaseModel):
    text: MessageText


class ConversationOut(BaseModel):
    id: UUID
    patient_ref: str
    status: str
    created_at: datetime


class MessageOut(BaseModel):
    role: str
    text: str
    intent: str | None
    outcome: str | None
    created_at: datetime


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]


class TurnOut(BaseModel):
    reply: str
    intent: str
    outcome: str
    status: str


def get_service(deps: Annotated[Deps, Depends(get_deps)]) -> ConversationService:
    return ConversationService(deps.pool, deps.clock, deps.locks, deps.workflow)


Service = Annotated[ConversationService, Depends(get_service)]


@router.post("", status_code=201)
def start_conversation(body: StartConversation, service: Service) -> ConversationOut:
    row = service.start(body.patient_ref)
    return ConversationOut(
        id=row.id, patient_ref=row.patient_ref, status=row.status, created_at=row.created_at
    )


@router.post("/{conversation_id}/messages")
def send_message(conversation_id: str, body: SendMessage, service: Service) -> TurnOut:
    result = service.handle_message(conversation_id, body.text)
    return TurnOut(
        reply=result.reply, intent=result.intent, outcome=result.outcome, status=result.status
    )


@router.get("/{conversation_id}")
def get_conversation(conversation_id: str, service: Service) -> ConversationDetail:
    row, rows = service.get(conversation_id)
    return ConversationDetail(
        id=row.id,
        patient_ref=row.patient_ref,
        status=row.status,
        created_at=row.created_at,
        messages=[
            MessageOut(
                role=m.role,
                text=m.text,
                intent=m.intent,
                outcome=m.outcome,
                created_at=m.created_at,
            )
            for m in rows
        ],
    )
