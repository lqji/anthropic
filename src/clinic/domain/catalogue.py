"""Match what a patient or the model wrote against the clinic's catalogue. Pure, no I/O."""

import re
from collections.abc import Sequence

_WORD = re.compile(r"\w+")
_TITLES = frozenset({"dr", "doctor"})


def name_tokens(name: str) -> set[str]:
    """Lower-case words of a name without titles, so "Dr. Salma" and "salma" compare equal."""
    return {word for word in _WORD.findall(name.lower()) if word not in _TITLES}


def match_name(query: str, names: Sequence[str]) -> str | None:
    """The catalogue name `query` stands for, or None if it names nobody or is ambiguous.

    Exact match first (ignoring case), then a unique name that contains every word of the query.
    """
    wanted = query.strip().lower()
    for name in names:
        if name.lower() == wanted:
            return name
    tokens = name_tokens(query)
    if not tokens:
        return None
    hits = [name for name in names if tokens <= name_tokens(name)]
    return hits[0] if len(hits) == 1 else None
