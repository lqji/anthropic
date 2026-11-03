"""Deterministic emergency detection. Runs before any LLM call; the LLM can never overrule it.

Rules err on the side of flagging: a false alarm only sends the emergency instruction, while a
missed emergency is dangerous. Negations ("no chest pain") therefore still match.
"""

import re

_PERSON = r"(?:my|his|her|their|our)"

# (rule name, phrases). Matched on normalised text (see `normalise`), whole words only.
# The first rule that matches names the emergency, so order goes from most to least specific.
_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "chest_pain",
        (
            r"chest (?:pain|pains|ache|hurts|hurting|pressure|tightness)",
            rf"(?:pain|pressure|tightness) (?:in|on) {_PERSON} chest",
            r"chest (?:is|feels) (?:tight|heavy|crushed)",
            r"heart attack",
        ),
    ),
    (
        "breathing",
        (
            r"cant (?:breathe|breath)",
            r"(?:struggling|unable|hard|difficult) to (?:breathe|breath)",
            r"difficulty breathing",
            r"(?:not|stopped) breathing",
            r"gasping for (?:air|breath)",
            r"choking",
        ),
    ),
    (
        "allergic_reaction",
        (
            r"anaphyla(?:xis|ctic)",
            r"(?:throat|tongue) (?:is )?(?:swelling|swollen|closing)",
        ),
    ),
    (
        "stroke",
        (
            r"(?:face|mouth) (?:is )?drooping",
            r"drooping (?:face|mouth)",
            r"slurred speech",
            r"slurring",
            r"(?:having|had) a stroke",
            r"stroke symptoms",
            rf"cant (?:move|feel) {_PERSON} (?:arm|leg|face|side)",
        ),
    ),
    (
        "unconscious",
        (
            r"unconscious",
            r"unresponsive",
            r"not responding",
            r"passed out",
            r"collapsed",
            r"(?:wont|cant) wake (?:up|him|her|them)",
        ),
    ),
    (
        "severe_bleeding",
        (
            r"bleeding (?:heavily|badly|everywhere|profusely|a lot)",
            r"(?:heavy|severe|uncontrolled|uncontrollable) bleeding",
            r"(?:wont|cant|will not) stop bleeding",
            r"lots? of blood",
        ),
    ),
    (
        "seizure",
        (
            r"seizures?",
            r"seizing",
            r"convulsions?",
            r"convulsing",
            r"(?:having|had) a fit",
        ),
    ),
    (
        "overdose_poisoning",
        (
            r"overdos(?:e|ed|ing)",
            r"(?:swallowed|drank|ate) (?:some |a )?(?:poison|bleach)",
            r"poisoned",
            r"too many (?:pills|tablets)",
        ),
    ),
    (
        "self_harm",
        (
            r"(?:kill|killing|hurt|hurting|harm|harming) (?:myself|himself|herself|themselves)",
            r"suicid(?:e|al)",
            rf"end {_PERSON} life",
            r"want to die",
        ),
    ),
)

_COMPILED: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(r"\b(?:" + "|".join(phrases) + r")\b")) for name, phrases in _RULES
)

RULE_NAMES: tuple[str, ...] = tuple(name for name, _ in _RULES)

_APOSTROPHES = re.compile(r"['‘’ʼ`]")
_NON_WORD = re.compile(r"[^a-z0-9]+")
_CANNOT = re.compile(r"\b(?:cannot|can not)\b")


def normalise(text: str) -> str:
    """Lowercase, drop apostrophes (can't -> cant), turn punctuation into spaces, collapse runs."""
    text = _APOSTROPHES.sub("", text.lower())
    text = _NON_WORD.sub(" ", text).strip()
    return _CANNOT.sub("cant", text)


def match_emergency(text: str) -> str | None:
    """Name of the first emergency rule the message matches, or None."""
    normalised = normalise(text)
    for name, pattern in _COMPILED:
        if pattern.search(normalised):
            return name
    return None


def is_emergency(text: str) -> bool:
    return match_emergency(text) is not None
