from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    """A clinic document section a reply was answered from (CFD-11)."""

    document: str
    heading: str


@dataclass(frozen=True)
class TurnResult:
    """What one conversation turn returns to the patient."""

    reply: str
    intent: str
    outcome: str
    status: str
    approval_id: str | None = None  # set when the turn is now waiting for staff approval
    sources: tuple[Source, ...] = ()  # empty unless the reply came from the clinic documents
