from clinic.workflows.nodes._stub import not_implemented
from clinic.workflows.state import ConversationState


def find_slots(state: ConversationState) -> dict[str, object]:
    """Stub (CFD-12 lists free slots and stores them in `offered_slots`)."""
    return not_implemented()


def book(state: ConversationState) -> dict[str, object]:
    """Stub (CFD-13 books an offered slot atomically; CFD-15 holds same-day slots)."""
    return not_implemented()


def confirm_booking(state: ConversationState) -> dict[str, object]:
    """Stub (CFD-13 confirms doctor, date and time to the patient)."""
    return {}
