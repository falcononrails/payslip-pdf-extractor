from __future__ import annotations

from payslip_extractor.matching import find_matching_numbers


def test_find_matching_numbers_uses_exact_digit_tokens() -> None:
    numbers = ["123", "456", "001"]
    number_set = set(numbers)

    text = "Matricule 123, CNSS 456, not 91234, leading 001."

    assert find_matching_numbers(text, numbers, number_set) == ("123", "456", "001")


def test_find_matching_numbers_does_not_match_substrings() -> None:
    numbers = ["123", "456"]
    number_set = set(numbers)

    assert find_matching_numbers("Values 91234 and 4567", numbers, number_set) == ()
