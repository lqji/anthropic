"""Which offered slot a patient's message picks ("book the first one"). Pure code, no LLM."""

import re
from typing import Final

_ORDINALS: Final = {
    "first": 1,
    "1st": 1,
    "second": 2,
    "2nd": 2,
    "third": 3,
    "3rd": 3,
    "fourth": 4,
    "4th": 4,
    "fifth": 5,
    "5th": 5,
}
# "number 2", "no. 2", "#2", "option 2", "slot 2". A time such as "10:00" has no such prefix.
_NUMBERED: Final = re.compile(r"(?:\bnumber|\bno\.?|#|\boption|\bslot)\s*(\d+)\b")
# The whole message is a number, optionally after "book": "2", "book 2."
_ONLY_NUMBER: Final = re.compile(r"(?:book\s+)?(\d+)\s*[.!]?")
_WORD: Final = re.compile(r"[a-z0-9]+")


def slot_choice(text: str) -> int | None:
    """1-based position in the offered list that `text` picks, or None if it names none."""
    lowered = text.lower().strip()
    numbered = _NUMBERED.search(lowered) or _ONLY_NUMBER.fullmatch(lowered)
    if numbered:
        return int(numbered[1]) or None
    for word in _WORD.findall(lowered):
        if word in _ORDINALS:
            return _ORDINALS[word]
    return None
