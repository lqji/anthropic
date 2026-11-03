from dataclasses import dataclass


@dataclass(frozen=True)
class TurnResult:
    """What one conversation turn returns to the patient."""

    reply: str
    intent: str
    outcome: str
    status: str
