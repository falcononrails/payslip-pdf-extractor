from __future__ import annotations

import re
from collections.abc import Sequence

_IDENTIFIER_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def find_matching_numbers(text: str, numbers: Sequence[str], number_set: set[str]) -> tuple[str, ...]:
    """Return identifiers present as exact tokens in page text."""
    if not text:
        return ()

    matched = {
        match.group(0)
        for match in _IDENTIFIER_TOKEN_RE.finditer(text)
        if match.group(0) in number_set
    }
    if not matched:
        return ()

    return tuple(number for number in numbers if number in matched)
