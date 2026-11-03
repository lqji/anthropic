from clinic.workflows.state import UNCLEAR, ConversationState


def classify(state: ConversationState) -> dict[str, object]:
    """Stub (CFD-07 classifies with the LLM). Until then every message is unclear."""
    return {"intent": UNCLEAR, "confidence": 0.0, "entities": {}}
