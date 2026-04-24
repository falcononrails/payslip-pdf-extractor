from __future__ import annotations

from payslip_extractor.matching import find_matching_numbers


def test_find_matching_numbers_uses_exact_tokens() -> None:
    numbers = ["123", "456", "001"]
    number_set = set(numbers)

    text = "Matricule 123, CNSS 456, not 91234, leading 001."

    assert find_matching_numbers(text, numbers, number_set) == ("123", "456", "001")


def test_find_matching_numbers_does_not_match_substrings() -> None:
    numbers = ["123", "456"]
    number_set = set(numbers)

    assert find_matching_numbers("Values 91234 and 4567", numbers, number_set) == ()


def test_find_matching_numbers_matches_alphanumeric_identifiers() -> None:
    numbers = ["456A", "B789", "123"]
    number_set = set(numbers)

    assert find_matching_numbers("Employee 456A and B789 here", numbers, number_set) == ("456A", "B789")


def test_find_matching_numbers_does_not_partial_match_alphanumeric() -> None:
    numbers = ["456"]
    number_set = set(numbers)

    assert find_matching_numbers("ID 456A and code A456", numbers, number_set) == ()


def test_find_matching_numbers_pure_digits_still_work() -> None:
    numbers = ["123", "456"]
    number_set = set(numbers)

    assert find_matching_numbers("Values 123 and 456", numbers, number_set) == ("123", "456")