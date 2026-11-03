"""`slot_choice`: which offered slot a message picks."""

import pytest

from clinic.domain.choice import slot_choice


@pytest.mark.parametrize(
    ("text", "choice"),
    [
        ("Book the first one", 1),
        ("book the FIRST one please", 1),
        ("I'll take the second", 2),
        ("the 3rd one", 3),
        ("Fifth, please", 5),
        ("book number 1", 1),
        ("Book number 4", 4),
        ("no. 2", 2),
        ("#3", 3),
        ("option 2 please", 2),
        ("slot 5", 5),
        ("2", 2),
        (" book 3. ", 3),
        ("number 7", 7),  # out of range for the offer: the caller checks
    ],
)
def test_a_choice_is_read_from_the_message(text: str, choice: int) -> None:
    assert slot_choice(text) == choice


@pytest.mark.parametrize(
    "text",
    [
        "Book me with Dr. Salma tomorrow",
        "Can I book at 10:00?",
        "book on 2026-11-03",
        "I want to book an appointment",
        "number 0",
        "",
    ],
)
def test_no_choice_without_an_ordinal_or_number(text: str) -> None:
    assert slot_choice(text) is None
