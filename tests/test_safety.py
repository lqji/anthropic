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
    ("I'm short of breath", "breathing"),
    ("Shortness of breath since this morning", "breathing"),
    ("I'm having trouble breathing", "breathing"),
    ("I couldn't breathe last night", "breathing"),
    ("I can't get my breath", "breathing"),
    ("My husband can’t catch his breath", "breathing"),
    ("Her breathing is difficult", "breathing"),
    ("Mum can´t breathe", "breathing"),  # acute accent used as an apostrophe
    ("I can′t breathe", "breathing"),  # prime used as an apostrophe
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
    # Phrasings a reviewer found missing: "the", other verbs, other wordings.
    ("My baby isn't breathing", "breathing"),
    ("he isnt breathing", "breathing"),
    ("she wasn't breathing", "breathing"),
    ("they aren't breathing", "breathing"),
    ("I can barely breathe", "breathing"),
    ("I can hardly breathe", "breathing"),
    ("my chest is hurting", "chest_pain"),
    ("my chest keeps hurting", "chest_pain"),
    ("I have pain in the chest", "chest_pain"),
    ("I think I'm having an allergic reaction", "allergic_reaction"),
    ("my lips are swelling", "allergic_reaction"),
    ("his face is swollen", "allergic_reaction"),
    ("the bleeding won't stop", "severe_bleeding"),
    ("the bleeding doesn't stop", "severe_bleeding"),
    ("blood everywhere", "severe_bleeding"),
    ("I don't want to live anymore", "self_harm"),
    ("I do not want to live", "self_harm"),
    ("he fainted", "unconscious"),
    ("she has fainted", "unconscious"),
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
    "Can I book a breathing exercise class?",
    "Do you offer a breath test for H. pylori?",
    "Is a spirometry breathing test covered by my insurance?",
    "I need a short appointment next week",
    "I can't make it tomorrow, can I cancel?",
    "I won't be able to come on Sunday",
    "I need an allergy test",
    "Can I book a scan of the chest?",
    "My lip balm ran out, do you sell any?",
    "The bleeding stopped yesterday, do I still need the check?",
    "I don't want to wait, can I come earlier?",
    "She isn't registered with you yet",
]

# Every apostrophe-like character that can stand in for ' in "can't".
APOSTROPHE_LIKE = [
    "'",
    "‘",
    "’",
    "‛",
    "ʼ",
    "ʹ",
    "´",
    "′",
    "‵",
    "ˊ",
    "ˋ",
    "＇",
    "`",
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


@pytest.mark.parametrize("apostrophe", APOSTROPHE_LIKE)
def test_every_apostrophe_variant_is_caught(apostrophe: str) -> None:
    assert match_emergency(f"I can{apostrophe}t breathe") == "breathing"
    assert match_emergency(f"She couldn{apostrophe}t breathe") == "breathing"


@pytest.mark.parametrize("text", ["I can t breathe", "I can-t breathe", "I can‑t breathe"])
def test_unknown_apostrophe_lookalike_still_reads_as_cant(text: str) -> None:
    # An apostrophe-like character we did not list turns into a space; "can t" is read as "cant".
    assert match_emergency(text) == "breathing"


def test_normalise_apostrophe_variants() -> None:
    assert normalise("can´t") == normalise("can′t") == normalise("can't") == "cant"
