from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class PayslipPeriod:
    year: int
    month: int

    def display(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"


_MONTH_NAMES = {
    "janvier": 1,
    "janv": 1,
    "january": 1,
    "jan": 1,
    "fevrier": 2,
    "fevr": 2,
    "february": 2,
    "feb": 2,
    "mars": 3,
    "march": 3,
    "mar": 3,
    "avril": 4,
    "avr": 4,
    "april": 4,
    "apr": 4,
    "mai": 5,
    "may": 5,
    "juin": 6,
    "june": 6,
    "jun": 6,
    "juillet": 7,
    "juil": 7,
    "july": 7,
    "jul": 7,
    "aout": 8,
    "august": 8,
    "aug": 8,
    "septembre": 9,
    "sept": 9,
    "september": 9,
    "sep": 9,
    "octobre": 10,
    "october": 10,
    "oct": 10,
    "novembre": 11,
    "november": 11,
    "nov": 11,
    "decembre": 12,
    "december": 12,
    "dec": 12,
}

_MONTH_NAME_PATTERN = "|".join(sorted(_MONTH_NAMES, key=len, reverse=True))
_YEAR = r"(?:19|20)\d{2}"
_MONTH = r"(?:0?[1-9]|1[0-2])"
_NUMERIC_MONTH_YEAR_RE = re.compile(rf"(?<!\d)(?P<month>{_MONTH})\s*(?:[/_.-]|\s)\s*(?P<year>{_YEAR})(?!\d)")
_NUMERIC_YEAR_MONTH_RE = re.compile(rf"(?<!\d)(?P<year>{_YEAR})\s*(?:[/_.-]|\s)\s*(?P<month>{_MONTH})(?!\d)")
_COMPACT_MONTH_YEAR_RE = re.compile(rf"(?<!\d)(?P<month>{_MONTH})(?P<year>{_YEAR})(?!\d)")
_COMPACT_YEAR_MONTH_RE = re.compile(rf"(?<!\d)(?P<year>{_YEAR})(?P<month>{_MONTH})(?!\d)")
_NAME_YEAR_RE = re.compile(rf"\b(?P<name>{_MONTH_NAME_PATTERN})\b\s+(?P<year>{_YEAR})\b")
_YEAR_NAME_RE = re.compile(rf"\b(?P<year>{_YEAR})\s+\b(?P<name>{_MONTH_NAME_PATTERN})\b")


def detect_payslip_period(*values: object) -> PayslipPeriod | None:
    candidates: list[PayslipPeriod] = []
    for value in values:
        if value is None:
            continue
        text = _normalize_text(str(value))
        candidates.extend(_numeric_periods(text))
        candidates.extend(_named_periods(text))

    return min(candidates) if candidates else None


def _numeric_periods(text: str) -> list[PayslipPeriod]:
    periods: list[PayslipPeriod] = []
    for pattern in (
        _NUMERIC_MONTH_YEAR_RE,
        _NUMERIC_YEAR_MONTH_RE,
        _COMPACT_MONTH_YEAR_RE,
        _COMPACT_YEAR_MONTH_RE,
    ):
        for match in pattern.finditer(text):
            period = _period_from_numbers(match.group("year"), match.group("month"))
            if period is not None:
                periods.append(period)
    return periods


def _named_periods(text: str) -> list[PayslipPeriod]:
    periods: list[PayslipPeriod] = []
    for pattern in (_NAME_YEAR_RE, _YEAR_NAME_RE):
        for match in pattern.finditer(text):
            month = _MONTH_NAMES.get(match.group("name"))
            if month is None:
                continue
            period = _period_from_numbers(match.group("year"), str(month))
            if period is not None:
                periods.append(period)
    return periods


def _period_from_numbers(year_value: str, month_value: str) -> PayslipPeriod | None:
    year = int(year_value)
    month = int(month_value)
    if not 1 <= month <= 12:
        return None
    return PayslipPeriod(year=year, month=month)


def _normalize_text(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value.casefold())
    without_accents = "".join(char for char in folded if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", without_accents)
