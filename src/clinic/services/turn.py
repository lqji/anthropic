from dataclasses import dataclass


@dataclass(frozen=True)
class TurnResult:
    """What one conversation turn returns to the patient."""

    reply: str
    intent: str
    outcome: str
    status: str
    approval_id: str | None = None  # set when the turn is now waiting for staff approval
