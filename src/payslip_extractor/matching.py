from __future__ import annotations

import re
from collections.abc import Sequence

_DIGIT_TOKEN_RE = re.compile(r"\d+")


def find_matching_numbers(text: str, numbers: Sequence[str], number_set: set[str]) -> tuple[str, ...]:
    """Return numbers present as exact digit tokens in page text."""
    if not text:
        return ()

    page_tokens = set(_DIGIT_TOKEN_RE.findall(text))
    if not page_tokens:
        return ()

    matched = page_tokens.intersection(number_set)
    if not matched:
        return ()

    return tuple(number for number in numbers if number in matched)
