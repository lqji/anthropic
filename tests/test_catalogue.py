import pytest

from clinic.domain.catalogue import match_name

DOCTORS = ("Dr. Salma Nasser", "Dr. Khalid Rahman", "Dr. Khalid Saleh")


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("Dr. Salma Nasser", "Dr. Salma Nasser"),
        ("dr. salma nasser", "Dr. Salma Nasser"),
        ("  Salma ", "Dr. Salma Nasser"),
        ("Dr Nasser", "Dr. Salma Nasser"),
        ("Khalid Rahman", "Dr. Khalid Rahman"),
        ("Khalid", None),  # two doctors share it: ambiguous
        ("Dr. Zaid", None),
        ("Dr.", None),
        ("", None),
    ],
)
def test_match_name(query: str, expected: str | None) -> None:
    assert match_name(query, DOCTORS) == expected


def test_match_name_on_an_empty_catalogue() -> None:
    assert match_name("Dr. Salma Nasser", ()) is None
