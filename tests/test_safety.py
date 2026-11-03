import pytest

from clinic.domain.safety import RULE_NAMES, is_emergency, match_emergency, normalise

EMERGENCIES = [
    ("I have crushing chest pain", "chest_pain"),
    ("I HAVE CRUSHING CHEST PAIN!!!", "chest_pain"),
    ("  i have   crushing\tchest\npain  ", "chest_pain"),
    ("chest-pain since an hour", "chest_pain"),
    ("There's a heavy pressure on my chest", "chest_pain"),
    ("My chest feels tight and heavy", "chest_pain"),
    ("I think my dad is having a heart attack", "chest_pain"),
    ("I can't breathe", "breathing"),
    ("I cant breathe", "breathing"),
    ("I can’t   breathe!", "breathing"),
    ("I cannot breathe properly", "breathing"),
    ("my son is struggling to breathe", "breathing"),
    ("She's not breathing", "breathing"),
    ("Baby is choking, help", "breathing"),
    ("My throat is swelling after eating peanuts", "allergic_reaction"),
    ("I think it's anaphylaxis", "allergic_reaction"),
    ("His face is drooping and he has slurred speech", "stroke"),
    ("I think my mother is having a stroke", "stroke"),
    ("I can't move my arm suddenly", "stroke"),
    ("My husband passed out and is unresponsive", "unconscious"),
    ("Grandpa collapsed in the kitchen", "unconscious"),
    ("The cut won't stop bleeding", "severe_bleeding"),
    ("He is bleeding heavily from his leg", "severe_bleeding"),
    ("My daughter is having a seizure", "seizure"),
    ("I took an overdose", "overdose_poisoning"),
    ("My toddler swallowed some bleach", "overdose_poisoning"),
    ("I swallowed too many pills", "overdose_poisoning"),
    ("I want to kill myself", "self_harm"),
    ("I'm feeling suicidal", "self_harm"),
    ("I want to end my life", "self_harm"),
]

ROUTINE = [
    "I need a blood test",
    "Do I need to fast before a blood test?",
    "Can I book a chest x-ray appointment?",
    "my child has a cold",
    "What are your opening hours?",
    "Cancel my appointment tomorrow",
    "Is there a breathing test appointment this week?",
    "My knee pain is much better now",
    "Does my insurance cover a skin check?",
    "Can I book a hearing aid fitting?",
    "I'd like a follow-up after my stroke rehabilitation last year",
    "I passed my driving test, can I get the medical form signed?",
    "Do you accept cash or card?",
    "Can my mother come with me to the consultation?",
]


@pytest.mark.parametrize(("text", "rule"), EMERGENCIES)
def test_emergency_phrasings_match_their_rule(text: str, rule: str) -> None:
    assert match_emergency(text) == rule
    assert is_emergency(text) is True


@pytest.mark.parametrize("text", ROUTINE)
def test_routine_messages_are_not_emergencies(text: str) -> None:
    assert match_emergency(text) is None
    assert is_emergency(text) is False


def test_enough_cases_for_the_acceptance_criteria() -> None:
    assert len(EMERGENCIES) >= 15
    assert len(ROUTINE) >= 10


def test_every_rule_is_exercised() -> None:
    assert {rule for _, rule in EMERGENCIES} == set(RULE_NAMES)


@pytest.mark.parametrize("text", ["", "   ", "\n\t", "?!..."])
def test_blank_text_is_not_an_emergency(text: str) -> None:
    assert match_emergency(text) is None


def test_negation_still_flags() -> None:
    # Deliberate: the rules never try to read negations, so they cannot talk themselves out of it.
    assert match_emergency("no chest pain, just checking") == "chest_pain"


def test_normalise() -> None:
    assert normalise("I CAN’T   breathe!!!") == "i cant breathe"
    assert normalise("I can not breathe") == "i cant breathe"
    assert normalise("chest-pain") == "chest pain"
