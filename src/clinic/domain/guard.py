"""Deterministic medical-advice guard: no diagnosis and no treatment advice (CFD-09).

Pure functions, no I/O. Two checks, both plain pattern matching so they never depend on a model:

- `asks_for_medical_advice` looks at the patient message. A match is declined before any LLM call.
- `guard_reply` looks at the reply about to be sent. A diagnosis, a treatment recommendation or a
  dosage is replaced by a safe fallback.

Patterns are a safety net next to the prompt rule, not a guarantee. They are English only and
lean towards blocking: a false alarm costs one polite redirect to a doctor.
"""

import re
from typing import Final

_MEDICINE = (
    r"(?:cream|creams|ointment|ointments|lotion|medicine|medicines|medication|medications|drug|drugs"
    r"|pill|pills|tablet|tablets|capsule|capsules|antibiotic|antibiotics|painkiller|painkillers"
    r"|antihistamine|antihistamines|steroid|steroids|supplement|supplements|vitamin|vitamins"
    r"|remedy|remedies|treatment|treatments|ibuprofen|paracetamol|acetaminophen|aspirin"
    r"|hydrocortisone|panadol|tylenol|insulin)"
)
_NOT_ADMIN = r"(?!\s+(?:to|for)\s+(?:my|the|our|your)\s+(?:visit|appointment|clinic|consultation))"
_NOT_AN_APPOINTMENT_TOPIC = (
    r"(?!(?:an?\s+|any\s+|the\s+|my\s+|some\s+)?"
    r"(?:appointments?|bookings?|visits?|slots?|insurance|co\s?pay|fees?|time|referral|results?"
    r"|blood|tests?|id|card|papers|documents|doctor|to|any|my|the)\b)"
)


def _normalise(text: str) -> str:
    """Lower case, no apostrophes, letters and digits only, single spaces."""
    text = text.lower().replace("’", "").replace("'", "")
    text = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", text)  # keep decimal points: "0.5 mg"
    return re.sub(r"[^a-z0-9.]+", " ", text).strip()


def _compile(patterns: dict[str, str]) -> dict[str, re.Pattern[str]]:
    return {name: re.compile(pattern) for name, pattern in patterns.items()}


# Rule name -> pattern, matched against the normalised patient message.
_ADVICE_REQUESTS: Final = _compile(
    {
        "treatment_request": (
            rf"\b(?:what|which) {_MEDICINE} (?:should|can|could|do|would|is|are|will|works?|helps?"
            r"|for|to|good|best)\b(?!.*\b(?:bring|list|show)\b)"
        ),
        "should_take": (
            r"\b(?:what|which|how much|how many|when)\b.{0,30}\bshould i (?:take|use|put|apply"
            rf"|drink|try|buy){_NOT_ADMIN}"
            r"|\bshould i (?:take|use|apply|put|stop taking|start taking|stop using|start using)\b"
            rf"{_NOT_ADMIN}"
        ),
        "can_take": (
            rf"\b(?:can|could|may|should) i (?:take|use|mix|combine|have) (?:any |some |an? )?"
            rf"{_MEDICINE}\b"
        ),
        "dosage": (
            r"\b(?:dose|doses|dosage|dosages)\b"
            r"|\bhow (?:much|many) (?:mg|ml|tablets?|pills?)\b"
        ),
        "how_to_treat": (
            r"\bhow (?:do|can|could|should|would) (?:i|we|you) (?:treat|cure|heal|fix|relieve|ease"
            r"|soothe|get rid of|stop|reduce)\b.{0,40}\b(?:rash|pain|ache|infection|fever|cough"
            r"|cold|flu|headache|migraine|swelling|itch|itching|burn|wound|allergy|symptoms?)\b"
            r"|\bhow (?:to|do i) (?:treat|cure|heal)\b|\bhome remed(?:y|ies)\b"
            r"|\bis it safe to (?:take|use|mix)\b"
        ),
        "what_to_do_about_symptom": (
            r"\bwhat should i (?:do|take|use) (?:about|for|if i have)\b"
            r"(?! (?:my|the) (?:appointment|booking|visit|insurance|payment|bill))"
        ),
        "diagnosis_request": (
            r"\bdiagnos(?:e|is|ed)\b|\bwhat(?:s| is) wrong with (?:me|my)\b"
            r"|\bwhat(?:s| is) (?:causing|the cause of) my\b"
            r"|\bwhat (?:disease|illness|condition|infection|allergy|sickness) (?:do i have"
            r"|is this|is it|could (?:it|this) be)\b"
            rf"|\b(?:do|did|will) i have {_NOT_AN_APPOINTMENT_TOPIC}"
            r"|\bam i (?:sick|ill|dying|pregnant|diabetic|infected|contagious|having a)\b"
            r"|\bis (?:it|this|that) (?:cancer|an? (?:infection|virus|allergy|tumou?r|disease"
            r"|sign of)|serious|dangerous|contagious|something serious)\b"
            r"|\bcould (?:it|this|that) be (?:cancer|an? (?:infection|virus|allergy|tumou?r"
            r"|disease)|diabetes|serious)\b"
            r"|\bwhy (?:does|do|is|am) (?:my|i) (?:\w+ ){0,2}(?:hurt|hurts|ache|aches|aching"
            r"|bleeding|swollen|itchy|itching|burning|numb|dizzy)\b"
        ),
    }
)

# Rule name -> pattern, matched against the normalised reply.
_ILLNESS = (
    r"(?:infection|virus|allergy|eczema|dermatitis|psoriasis|diabetes|cancer|asthma|flu|influenza"
    r"|cold|migraine|condition|disease|disorder|syndrome|tumou?r|ulcer|anaemia|anemia|arthritis"
    r"|hypertension|depression|covid|pneumonia|bronchitis|sinusitis|tonsillitis|gastritis|rash)"
)
_REPLY_RULES: Final = _compile(
    {
        "diagnosis_wording": r"\bdiagnos(?:e|es|ed|is)\b",
        "diagnosis_claim": (
            rf"\byou (?:probably |likely |possibly |may |might |could |seem to |appear to )?"
            rf"(?:have|are suffering from|suffer from|got) (?:an? |the )?(?:\w+ ){{0,2}}"
            rf"{_ILLNESS}\b"
        ),
        "diagnosis_guess": (
            rf"\b(?:this|it|that|your symptoms?) (?:looks|sounds|seems|appears) (?:like|to be)"
            rf" (?:an? |the )?(?:\w+ ){{0,2}}{_ILLNESS}\b"
            rf"|\byour symptoms? (?:suggest|indicate|point to|are consistent with|are typical of)\b"
            rf"|\b(?:it|this) (?:is|could be|might be|may be|is probably|is likely) (?:an? |the )?"
            rf"(?:\w+ ){{0,2}}{_ILLNESS}\b"
        ),
        "treatment_advice": (
            rf"\b(?:apply|take|use|try|put|rub|swallow|drink) (?:\w+ ){{0,3}}{_MEDICINE}\b"
            r"|\b(?:i|we) (?:recommend|suggest|advise|prescribe)\b(?! (?:booking|that you book"
            r"|you book|calling|to book|to call|arriving|that you arrive|bringing|checking in))"
            r"|\byou (?:should|must|need to|ought to|had better) (?:take|use|apply|put|try|rest"
            r"|stop taking|start taking|see a doctor immediately)\b"
        ),
        "dosage": (
            r"\b\d+(?:\.\d+)? ?(?:mg|mcg|ml|iu|tablets?|pills?|capsules?|drops)\b"
            r"|\b(?:once|twice|\d+ times) (?:a|per) day\b"
        ),
    }
)


def asks_for_medical_advice(text: str) -> bool:
    """True if the patient asks for a diagnosis, a treatment or a medicine recommendation."""
    return matched_advice_request(text) is not None


def matched_advice_request(text: str) -> str | None:
    """Name of the first advice-request rule that matches the patient message, if any."""
    normalised = _normalise(text)
    for name, pattern in _ADVICE_REQUESTS.items():
        if pattern.search(normalised):
            return name
    return None


def violated_rule(reply: str) -> str | None:
    """Name of the first rule a reply breaks (diagnosis, treatment advice, dosage), if any."""
    normalised = _normalise(reply)
    for name, pattern in _REPLY_RULES.items():
        if pattern.search(normalised):
            return name
    return None


def guard_reply(reply: str, fallback: str) -> tuple[str, str | None]:
    """The reply to send and the rule that replaced it: `(reply, None)` when it is safe."""
    rule = violated_rule(reply)
    if rule is None:
        return reply, None
    return fallback, rule
