from typing import Final

NOT_IMPLEMENTED: Final = "not_implemented"
NOT_IMPLEMENTED_REPLY: Final = (
    "Sorry, I can't help with that here yet. Please call the front desk during opening hours."
)


def not_implemented() -> dict[str, object]:
    """Update of a stub node: an honest fallback reply. Replaced as each story lands."""
    return {"reply": NOT_IMPLEMENTED_REPLY, "outcome": NOT_IMPLEMENTED}
